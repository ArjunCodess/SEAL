"""Executor-signed per-principal receipts with independently signed checkpoints.

This detects post-observation transcript tampering under pinned keys, a trusted
latest checkpoint/roster, and an honest executor. It does not prove task correctness
or detect an executor that lies before a checkpoint is created.
"""
from __future__ import annotations

import base64
from collections import defaultdict
import hashlib
import json
from threading import RLock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ZERO='0'*64


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')


def signed(value,key):
    return {'payload':value,'signature':base64.b64encode(key.sign(canonical(value))).decode('ascii')}


def authentic(packet,key):
    try:
        key.verify(base64.b64decode(packet['signature'],validate=True),canonical(packet['payload']))
        return True
    except (InvalidSignature,ValueError,KeyError,TypeError):return False


def digest(packet):return hashlib.sha256(canonical(packet)).hexdigest()


class ExecutorLog:
    """The caller supplies authenticated principal identity, not an agent's claim."""
    def __init__(self,session,executor_key=None,checkpoint_key=None):
        self.session=session
        self.executor_key=executor_key or Ed25519PrivateKey.generate()
        self.checkpoint_key=checkpoint_key or Ed25519PrivateKey.generate()
        self.heads={};self.receipts=[];self.version=0;self._lock=RLock()

    def append(self,principal,alias,event):
        with self._lock:
            principal=str(principal)
            head=self.heads.get(principal,{'count':0,'digest':ZERO})
            packet=signed({'profile':'aliasscope-receipt-v1','session':self.session,'principal':str(principal),
                           'alias':str(alias),'sequence':head['count']+1,'previous_digest':head['digest'],
                           'event':json.loads(canonical(event))},self.executor_key)
            self.heads[principal]={'count':head['count']+1,'digest':digest(packet)}
            self.receipts.append(packet)
            return packet

    def checkpoint(self):
        with self._lock:
            self.version+=1
            # The checkpoint key represents an independent observer retaining this
            # roster/root outside writable transcript storage, not a second agent key.
            return signed({'profile':'aliasscope-checkpoint-v1','session':self.session,
                           'version':self.version,'heads':json.loads(json.dumps(self.heads))},self.checkpoint_key)

    @property
    def public_keys(self):
        return self.executor_key.public_key(),self.checkpoint_key.public_key()


def verify(receipts,executor_public_key,checkpoint_public_key,session,checkpoint=None,minimum_version=0):
    issues=[];groups=defaultdict(list)
    def issue(kind,principal=None,sequence=None):issues.append({'kind':kind,'principal':principal,'sequence':sequence})
    for packet in receipts:
        payload=packet.get('payload',{}) if isinstance(packet,dict) else {}
        if not isinstance(payload,dict):issue('malformed_receipt');continue
        principal=payload.get('principal');sequence=payload.get('sequence')
        if not isinstance(principal,str) or type(sequence) is not int or sequence<1:
            issue('malformed_receipt');continue
        if payload.get('profile')!='aliasscope-receipt-v1':issue('wrong_receipt_profile',principal,sequence)
        if payload.get('session')!=session:issue('session_mismatch',principal,sequence)
        if not authentic(packet,executor_public_key):issue('invalid_executor_signature',principal,sequence)
        groups[principal].append(packet)
    trusted_heads=None
    if checkpoint is not None:
        if not authentic(checkpoint,checkpoint_public_key):issue('invalid_checkpoint_signature')
        else:
            payload=checkpoint['payload']
            if payload.get('profile')!='aliasscope-checkpoint-v1':issue('wrong_checkpoint_profile')
            elif payload.get('session')!=session:issue('checkpoint_session_mismatch')
            elif type(payload.get('version')) is not int or payload['version']<minimum_version:issue('stale_checkpoint')
            else:
                heads=payload.get('heads')
                if not isinstance(heads,dict) or not all(isinstance(k,str) and isinstance(v,dict) and
                    type(v.get('count')) is int and v['count']>=0 and isinstance(v.get('digest'),str) and
                    len(v['digest'])==64 for k,v in heads.items()):issue('malformed_checkpoint')
                else:trusted_heads=heads
    for principal,packets in groups.items():
        sequences=[p['payload']['sequence'] for p in packets]
        if sequences!=sorted(sequences):issue('per_principal_reordering',principal)
        counts=defaultdict(list)
        for packet in packets:counts[packet['payload']['sequence']].append(packet)
        for seq,items in counts.items():
            if len(items)>1:issue('duplicate_sequence',principal,seq)
        # Without an anchor, completeness is known only up to the largest seen seq.
        maximum=trusted_heads[principal]['count'] if trusted_heads and principal in trusted_heads else max(counts)
        # Bound work by the received log size, even if an attacker claims a huge
        # sequence number. Long missing runs are represented as intervals.
        previous=0
        for seq in sorted([s for s in counts if s<=maximum])+[maximum+1]:
            if seq>previous+1:
                if seq-previous-1<=1024:
                    for missing in range(previous+1,seq):issue('missing_sequence',principal,missing)
                else:issues.append({'kind':'missing_sequence_range','principal':principal,'start':previous+1,'end':seq-1})
            previous=seq
        for seq,items in counts.items():
            packet=items[0]
            previous=ZERO if seq==1 else digest(counts[seq-1][0]) if seq-1 in counts else None
            if previous is not None and packet['payload'].get('previous_digest')!=previous:
                issue('broken_chain_link',principal,seq)
        if trusted_heads is not None:
            if principal not in trusted_heads:issue('uncheckpointed_principal',principal)
            else:
                expected=trusted_heads[principal]
                if max(counts)!=expected['count']:issue('checkpoint_count_mismatch',principal)
                if expected['count'] in counts and digest(counts[expected['count']][0])!=expected['digest']:
                    issue('checkpoint_head_mismatch',principal,expected['count'])
    if trusted_heads is not None:
        for principal,head in trusted_heads.items():
            if principal not in groups:
                if head['count']<=1024:
                    for sequence in range(1,head['count']+1):issue('missing_sequence',principal,sequence)
                else:issues.append({'kind':'missing_sequence_range','principal':principal,'start':1,'end':head['count']})
    return {'valid':not issues,'issues':issues,'externally_anchored':trusted_heads is not None,
            'guarantee':'Post-observation integrity only, assuming trusted executor, pinned keys, and authentic fresh external checkpoints.'}


def public_bytes(key):return base64.b64encode(key.public_bytes_raw()).decode('ascii')


def public_from_bytes(value):return Ed25519PublicKey.from_public_bytes(base64.b64decode(value,validate=True))
