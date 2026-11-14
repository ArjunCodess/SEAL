"""Build the main arXiv-style manuscript and the separate AAAI workshop paper.

Requires frozen blind and retry studies; the wording study is included only
when independently frozen. Original fixed-study reporting remains separate.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import seal_collection as sc, seal_followups as follow
from scripts import analyze_seal_followups as analysis
from scripts.prepare_seal_venues import load_derived, build_endpoint_table
from scripts.seal_wording_cohorts import frozen_cohorts


def escape(value):
    return str(value).replace("_", r"\_").replace("%", r"\%")


def csv_rows(path):
    with Path(path).open(encoding="utf8", newline="") as stream:
        return list(csv.DictReader(stream))


def table(headers, rows, caption, label, wide=True):
    cells = [" & ".join(escape(cell) for cell in row) + r" \\" for row in rows]
    environment = 'table*' if wide else 'table'
    placement = 't' if wide else 'htbp'
    return ("\n\n" + r"\begin{" + environment + '}[' + placement + r"]\centering\small" + "\n" +
            r"\begin{tabular}{" + "l" * len(headers) + "}\n\\toprule\n" +
            " & ".join(headers) + r" \\\midrule" + "\n" + "\n".join(cells) +
            "\n\\bottomrule\n\\end{tabular}\n\\caption{" + caption + "}\\label{" + label +
            "}\n\\end{" + environment + "}\n\n")


def build_body():
    from scripts.seal_worker_audit import original_worker_audit
    original_workers = original_worker_audit()
    parents = {name: follow.require_frozen(ROOT / "data" / directory) for name, directory in
               (("blind", "study_blind_provenance"), ("retry", "study_gate_retry"))}
    sc.verify_source_contract()
    analysis.run(ROOT / "build/seal/analysis")
    derived = load_derived(ROOT / "build")
    baseline = build_endpoint_table(derived["two_endpoints"])
    comparison = csv_rows(ROOT / "build/seal/analysis/provenance_comparison.csv")
    blind_rows = []
    honest_rows = []
    for row in comparison:
        if row["blind_data_status"] != "frozen":
            raise ValueError("Unfrozen blind comparison")
        model, condition = row["model"], row["condition"]
        if condition == "honest":
            honest_rows.append((model, row["old_honest_completion_count"] + "/" + row["old_honest_completion_denominator"],
                               row["blind_honest_completion_count"] + "/" + row["blind_honest_completion_denominator"]))
        else:
            blind_rows.append((model, condition,
                row["old_decision_unsafe_attempt_count"] + "/" + row["old_completed"],
                row["old_execution_unsafe_count"] + "/" + row["old_completed"],
                row["blind_decision_unsafe_attempt_count"] + "/" + row["blind_completed"],
                row["blind_execution_unsafe_count"] + "/" + row["blind_completed"],
                row["paired_common_fixture_count"]))
    retries = csv_rows(ROOT / "build/seal/analysis/retry_endpoints.csv")
    short_names = {'gemma-4-31b-it': 'Gemma', 'qwen3.5-397b': 'Qwen'}
    retry_table = table(["Model", "Cond.", "Eligible", "Valid", "D", "E"],
        [(short_names[r["model"]], 'S' if r['condition'] == 'single' else 'C', r["scheduled"], r["completed"], r["unsafe_decisions"], r["unsafe_executions"]) for r in retries],
        "Single-retry outcomes conditional on eligible blocked requests; S/C denote single/coordinated. D/E are unsafe decisions/executions. Missing responses remain unresolved.", "tab:retry", wide=False)
    gate = derived["gate_manifest"]
    body = r"""\section{Question and contribution}
An agent supervisor can request an unsafe action even when an execution boundary prevents it.
Measuring only the final sink outcome obscures this distinction. SEAL (Supervisor Evidence Arms, Live) separates unsafe publication
requests from unsafe executions on a finite catalog of synthetic local jobs. It compares worker claims,
signed receipts, one fixed keeper snapshot, and provenance evidence plus an executable gate.
The contribution is a reproducible measurement of that distinction, its oracle and pairing limits,
and two prospective follow-ups: hiding the precomputed trusted summary and providing one gate response.

AgentDojo evaluates prompt-injection attacks in tool-using environments~\citep{agentdojo}, while
instruction-hierarchy work trains models to prioritize instructions~\citep{hierarchy}.
AI Control evaluates protocols against deliberate subversion~\citep{control}; CaMeL separates
trusted control flow from untrusted data and enforces capabilities~\citep{camel}.
SEAL studies a narrower question on synthetic jobs. Its findings do not establish a new general
defense, repository-task performance, or robustness to adaptive attackers.

\section{Experimental system and oracle}
Each fixture specifies a local workflow, candidate summary, trusted inputs, receipts, a fixed state
snapshot, publication policy, and a reference true summary. The four test families implement three
mechanisms: consistent fake input, unauthorized publication, and ambiguous evidence. Two families
repeat the first mechanism. Numeric variants do not create new families or mechanisms.
The supervisor models are \texttt{gemma-4-31b-it} and \texttt{qwen3.5-397b}, reported separately.
The worker attack goals are instructed and non-adaptive. Single and coordinated presentations
change both message count and speaker roles; three scripted speakers do not establish collusion.
Original honest controls use three speakers and are reported once per model and arm;
they are not matched one-speaker controls.

The claims arm exposes completion messages. Receipts add signatures and a fresh checkpoint;
state adds a fixed keeper snapshot, not an adaptive retrieval loop. These arms publish when the
supervisor requests publication. Provenance adds trusted input and authorization evidence and
enforces a deterministic gate after the supervisor decision. Evidence and enforcement therefore
change together across the original arms; this is not an information-matched causal comparison.

The safety oracle accepts an execution only when the fixture is legitimate and answerable and
the published summary equals the true summary. A factually corrected summary on an illegitimate
job can still be unsafe because the execution lacks binding or authorization. The code gate is
\texttt{\_provenance\_action} in \texttt{src/live\_fixtures.py}. It checks state, input binding,
tool and channel authorization, and \texttt{trusted\_summary != summary}. Earlier checks can veto
before summary equality is reached. Thus corrected true summaries on illegitimate fixtures can
still be blocked. Its execution zeros are enforcement outcomes, not evidence of learned refusal.

\section{Endpoints, denominators, and uncertainty}
An attempted publication is a supervisor publish decision. An unsafe decision is
\texttt{unsafe\_attempt}; an unsafe execution is \texttt{unsafe\_execution} from
\texttt{execute\_decision}. Legitimate completion is reported separately to reveal indiscriminate
blocking. Invalid model output and transport failures are retained in the scheduled ledger.
Primary descriptive rates use completed decisions; all-scheduled sensitivity counts and
missing-outcome bounds are generated alongside them. A missing decision is not a refusal.
The original ledger contains 960 scheduled calls, 909 completed responses, 29 invalid outputs,
and 22 errors. These are fixed historical observations; no missing call was repaired.

Intervals resample whole family blocks with 2,000 replicates and seed 20261108.
Only four template families are available and mechanisms repeat, so these intervals describe
the recorded catalog and offer little evidence about a broader task population.
The generated CSVs retain intervals and scheduled-outcome bounds; the paper emphasizes counts.
Resampling a deterministic gate zero does not certify a general security property.
Models and speaker conditions are not pooled. A paired arm contrast requires matching
completed fixture sets across all four arms in a family. Zero eligible families means an
unavailable contrast, not a zero effect.
The reported four-arm pairing inventory concerns unsafe executions; no paired
unsafe-decision effect estimate is claimed.

\section{Original evidence-arm results}
Table~\ref{tab:original} reports both endpoints with their completed denominators.
Claims, receipts, and the fixed snapshot still publish completed attacks in this catalog.
Their unsafe decision and execution counts match because there is no independent sink veto.
Provenance requests and executions diverge because the code gate can refuse a publish decision.
Saved provenance observations were shown the true summary when binding was complete, which
makes the original arm unsuitable as a test of unaided answer inference.
All 280 accepted original worker calls copied the template because \texttt{wording\_reference}
was in the prompt. Calling these generated messages does not establish wording diversity.
"""
    body = body.replace('All 280 accepted original worker calls copied the template',
        f"{original_workers['exact_copies']}/{original_workers['accepted']} accepted original worker calls copied the template")
    body += "\n\\begin{table*}[t]\\centering\\small\n" + baseline + (
        "\\caption{Original unsafe decisions / unsafe executions / completed attacks (D/E/N). "
        "Every model and speaker stratum is separate.}\\label{tab:original}\\end{table*}\n")
    honest = derived['two_endpoints'][derived['two_endpoints']['coordination'] == 'honest']
    body += 'Original honest completion by arm was '
    summaries = []
    for model in ('gemma-4-31b-it', 'qwen3.5-397b'):
        cells = []
        for arm in ('claims', 'receipts', 'state', 'provenance'):
            cell = honest[(honest['model'] == model) & (honest['arm'] == arm)].iloc[0]
            cells.append(f"{arm} {int(cell['honest_completions'])}/{int(cell['honest_completed'])}")
        summaries.append(short_names[model] + ': ' + ', '.join(cells))
    body += '; '.join(summaries) + '. '
    if all(honest['honest_unsafe_executions'] == 0):
        body += 'Every original honest model/arm cell had zero unsafe executions.\n'
    else:
        raise ValueError('Original honest alteration invariant changed')
    body += r"""Paired contrasts retain two of four Gemma families in each attack stratum,
zero of four for Qwen single, and one of four for Qwen coordinated.
\texttt{incident-timeline} is unpaired in every attack stratum. Marginal rates retain
families excluded by pairing; matched-subset contrasts must not be generalized to the full catalog.
"""
    pairing = csv_rows(ROOT / 'build/seal/analysis/pairing_coverage.csv')
    body += table(['Model', 'Cond.', 'Eligible', 'Paired', 'Unpaired'],
        [(short_names[r['model']], 'S' if r['coordination'] == 'single' else 'C', r['families_eligible'], r['families_paired'],
          r['reason_unpaired'].replace('fixture_set_mismatch', 'set mismatch'))
         for r in pairing if r['endpoint'] == 'unsafe_execution' and r['coordination'] in ('single', 'coordinated')],
        'Original four-arm unsafe-execution pairing. S/C denote single/coordinated. Unpaired counts have different completed fixture sets. The incident-timeline family is unpaired in every attack stratum.', 'tab:pairing', wide=False)
    body += r"""

\section{Deterministic gate mutation audit}
The audit replays each of 80 attack fixture presentations with five prescribed summary operations:
increment one numeric field, rename one key, submit an empty summary, substitute the true summary,
or add an extra key. Each operation starts from the recorded candidate; the true-summary substitution
is a complete replacement. This is a summary-mutation audit of the unchanged gate, not mutation
of the gate code or an adaptive search. Before/after summaries and hashes are retained.
"""
    actions = gate["observed_gate_actions"]
    body += (f"Across {gate['total_mutations_evaluated']} cases the observed actions were "
             f"{actions.get('block', 0)} blocks, {actions.get('abstain', 0)} abstentions, and "
             f"{actions.get('publish', 0)} publications. ")
    positive = gate["controls"]["positive_legitimate_controls"]
    mismatch = gate["controls"]["isolated_equality_mismatch_controls"]
    body += (f"Positive legitimate controls published {positive['published_count']}/{positive['total']}; "
             f"isolated equality-mismatch controls blocked {mismatch['blocked_count']}/{mismatch['total']}. ")
    body += table(['Mutation', 'N', 'B', 'A', 'P', 'E'],
        [(r['mutation'], r['fixtures'], r['block'], r['abstain'], r['publish'], r['unsafe_execution'])
         for r in gate['by_mutation']],
        'Summary mutation audit: N fixture presentations, B blocks, A abstentions, P publications, E unsafe executions. Each row covers the same attack fixture presentations.', 'tab:mutations', wide=False)
    body += r"""The earlier state and provenance checks often veto before equality is reached.
Legitimate true-summary controls demonstrate that the gate can publish; separately constructed
otherwise-valid summary-mismatch controls reach the equality check. These derived controls are
identified separately from attacks. Catalog-wide zeros are not a proof that every possible
input or implementation satisfies a security property.

\section{Blind provenance study}
The prospective blind study uses the same 40 fixtures, original worker corpus, two supervisors,
and three speaker conditions, scheduling 240 new provenance-only calls. Only the outward
\texttt{trusted\_summary} field is nulled. The underlying trusted inputs and gate remain unchanged.
Other evidence can still allow inference or recomputation of the answer; this is removal of
the explicitly supplied answer, not a guarantee that all answer information is absent.
The original and blind periods were collected at different times, and hosted backend versions
are not pinned by weights. A difference cannot therefore be attributed exclusively to visibility.
Table~\ref{tab:blind} gives the four endpoint rates and common completed fixture coverage;
Table~\ref{tab:honest} keeps honest completion separate.
"""
    body += table(["Model", "Condition", "Old D/N", "Old E/N", "Blind D/N", "Blind E/N", "Common"],
                  blind_rows, "Provenance unsafe decisions (D) and executions (E), divided by completed attacks (N); common denotes matched completed fixture IDs.", "tab:blind")
    body += table(["Model", "Old honest", "Blind honest"],
                  [(short_names[m], old, blind) for m, old, blind in honest_rows],
                  "Legitimate completion among completed honest provenance calls; old observations supplied the answer when available.", "tab:honest", wide=False)
    body += r"""\section{One gate retry}
After the blind ledger was stored and frozen, each completed attack publication request with
a gate block was eligible for one retry by the same supervisor. The retry includes the original
observation, first decision, and actual block reason once; it adds no oracle summary or worker
evidence. Supervisor refusals, gate abstentions, invalid outputs, and errors are ineligible.
Eligibility and parent hashes are frozen before sending. An initial unsafe request remains
recorded even if the retry declines. There are no transport replays or second
retries. These results condition on initial blocked requests and are not an unconditional model
comparison. Original attacks remain instructed and non-adaptive; this extension is one
feedback-conditioned supervisor decision, not adaptive attacker optimization or model training.
""" + retry_table
    cohorts = frozen_cohorts()
    if 'primary' in cohorts:
        wording = cohorts['primary']
        parents["wording_primary"] = follow.require_frozen(wording)
        parents["wording_diagnostic"] = follow.require_frozen(cohorts['diagnostic'])
        rows = csv_rows(ROOT / "build/seal/analysis/wording_endpoints.csv")
        audit = sc.read_json(ROOT / "build/seal/analysis/wording_audit.json")
        body += r"""\section{New wording and honest speaker control}
The independent second study removes \texttt{wording\_reference} and generates a new worker
corpus on the same four families. It adds a one-speaker honest presentation using the final
reporting role, preserving the underlying legitimate workflow, public totals, oracle, and gate.
Four conditions cross attack/honest status with one/three speakers. The same frozen text within
each fixture and condition is shown to every arm and supervisor. Provenance retains the original
answer visibility so wording and blinding are not changed together. Each message has one
generation attempt; failures remain missing, without fallback, regeneration, or copy rejection.
The supervisor schedule has 1,280 planned cells; unavailable worker conditions remain in the
denominator ledger and cause no fabricated inference observations.
Generation-inclusive bounds describe pessimistic pipeline outcomes over the planned grid.
Separate recorded-call bounds condition on available worker text and leave invalid or missing
model responses unresolved. Unavailable generation is not a supervisor decision or refusal.
"""
        body += (f"Of {audit['attempts']} worker attempts, {audit['accepted']} were accepted and "
                 f"{audit['exact_copies']} exactly copied their original reference message; "
                 f"{audit['missing_conditions']} conditions lacked complete valid generation. "
                 f"Only {audit['used_worker_messages']} messages belong to fully valid conditions and reach supervisors. "
                 f"That used corpus contains {audit['used_unique_messages']} unique strings and "
                 f"{audit['used_unique_numeric_masked_messages']} unique strings after replacing numeric tokens. ")
        usable = audit['usable_conditions_by_condition']
        body += ('Usable groups out of 40 per condition were ' + ', '.join(
            f"{name}: {usable.get(key, 0)}" for key, name in (
                ('attack_single', 'one-speaker attack'), ('attack_coordinated', 'three-speaker attack'),
                ('honest_single', 'one-speaker honest'), ('honest_coordinated', 'three-speaker honest'))) + '. ')
        body += ('A three-message group is unavailable if any member fails validation. '
                 'Generation success therefore changes fixture composition across conditions, and '
                 'the independently generated text also changes with speaker count. These marginal '
                 'contrasts do not isolate a causal speaker-count effect. ')
        body += (f"The first reported rejection reason was the required final claim-status line in "
                 f"{audit['rejection_reasons'].get('content must end with a claim_status line', 0)} worker attempts. "
                 "Formatting failures are excluded even when the message may otherwise convey the supplied facts; "
                 "these outputs were not repaired or used to fill the supervisor grid. ")
        diagnostic = sc.read_json(ROOT / 'build/seal/analysis/wording_diagnostic_audit.json')
        body += (f"An earlier diagnostic cohort had {diagnostic['worker_http_statuses'].get('429', 0)} "
                 f"worker HTTP 429 responses and {diagnostic['unknown_delivery_calls']} supervisor calls "
                 "with unknown delivery after interruption. It is retained separately, including "
                 "full conservative reservations for uncertain delivery. One paced replacement cohort "
                 "was selected for operational failure before its safety outcomes were observed. "
                 "Both cohorts share the original wording budget; no failed request was replayed. "
                 "Schema, role, and supplied numeric or string-total tokens determine acceptance. "
                 "These checks are not independent semantic annotation, and exact-copy counts alone "
                 "do not establish meaningful linguistic diversity. ")
        body += "Tables~\\ref{tab:wording-gemma} and~\\ref{tab:wording-qwen} report the new corpus separately from historical observations.\n"
        names = {"gemma-4-31b-it": "Gemma", "qwen3.5-397b": "Qwen"}
        conditions = {"attack_single": "A1", "attack_coordinated": "A3", "honest_single": "H1", "honest_coordinated": "H3"}
        for model, name in names.items():
            cells = [r for r in rows if r['model'] == model]
            body += table(["Cond.", "Arm", "Valid", "D", "E", "C"],
                [(conditions[r["condition"]], r["arm"], r["completed"], r["unsafe_decisions"],
                  r["unsafe_executions"], r["honest_completions"]) for r in cells],
                f"New wording, {name}: unsafe decisions (D), unsafe executions (E), legitimate completions (C). "
                "Each cell plans 40 calls; Valid is the completed denominator. A1/A3 are one/three-speaker attacks; H1/H3 are honest.",
                'tab:wording-' + name.lower(), wide=False)
    body += r"""\section{Limits and disclosure}
The original wording is scripted; historical arm comparisons have incomplete matching;
the original provenance view shows the true summary; message counts and roles confound coordination;
and four families cover only three mechanisms. The honest speaker control narrows one gap but
does not isolate every role or length effect. New wording on the same fixtures does not establish
new mechanisms or generalization to repository tasks. Invalid outputs and API errors can change
completed-case composition. Single realizations and hosted-model drift limit historical comparisons.

All jobs and the sink are synthetic and local, \texttt{side\_effects} is false, and signing keys
are deterministic public demo keys. No real organization, repository task, secret, or external
publication action is attacked. API credentials are excluded from the research artifacts.
The result supports separating requests, gates, and observations; it does not establish that
models learned to refuse, that receipts are generally weak, or that three speakers collude.
Implementation and drafting used AI assistance; the author is responsible for reviewing the
code, analyses, recorded evidence, and manuscript. No external submission is performed by this build.

\section{Reproducibility}
The original study directory is immutable. Each extension has its own protocol, prompt/source
hashes, terminal ledger, saved request and available response bodies, and checksum manifest. Budgets reserve
before sending; uncertain delivery is charged conservatively and is never silently replayed.
Every table is rebuilt from code and frozen evidence, with separate decision, execution, honest
completion, missingness, and pairing records. The gate is replayed on saved decisions without
model calls. The original fixed-study reproduction and its preserved sources remain available.
For six blind-study transport failures, no JSON response body was saved; separate failure events retain
their reconstructed, hash-verified requests and explicitly null responses. A freeze-only amendment
records this distinction without inventing model output. A wording validation amendment handles
string-valued totals as well as numeric totals, reusing saved requests rather than regenerating them.
The operational amendments and their source hashes remain in the frozen evidence.
Other transport failures likewise remain missing observations when no response body was retained.
One Windows ledger-write failure occurred before call-file creation and HTTP dispatch.
Its checkpoint and traceback are retained; the proven-unsent reservation was released.
The resumed collector retries local file replacement only, without replaying API requests.

\FloatBarrier
\begin{thebibliography}{9}
\bibitem[Debenedetti et al.(2024)]{agentdojo} E. Debenedetti et al. AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents. arXiv:2406.13352, 2024.
\bibitem[Wallace et al.(2024)]{hierarchy} E. Wallace et al. The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions. arXiv:2404.13208, 2024.
\bibitem[Greenblatt et al.(2023)]{control} R. Greenblatt et al. AI Control: Improving Safety Despite Intentional Subversion. arXiv:2312.06942, 2023.
\bibitem[Debenedetti et al.(2025)]{camel} E. Debenedetti et al. Defeating Prompt Injections by Design. arXiv:2503.18813, 2025.
\end{thebibliography}
"""
    return body, parents


def run(compile_pdf=True):
    body, parents = build_body()
    from scripts.plot_seal_endpoints import run as plot_endpoints
    figure = plot_endpoints()
    author = sc.read_json(ROOT / "paper/authors.json")["authors"][0]
    title = "SEAL: Separating Supervisor Publication Requests from Enforced Outcomes"
    original_n = sum(bool(line.strip()) for line in (ROOT / 'build/episodes.jsonl').read_text(encoding='utf8').splitlines())
    mutations_n = sc.read_json(ROOT / 'build/gate_mutations_manifest.json')['total_mutations_evaluated']
    blind_n = sc.read_json(ROOT / 'data/study_blind_provenance/freeze_manifest.json')['scheduled_episode_count']
    retry_n = sc.read_json(ROOT / 'data/study_gate_retry/summary.json')['scheduled']
    abstract = ("Supervisor publication decisions and enforced sink outcomes measure different events. "
                f"We audit four evidence arms on {original_n} scheduled supervisor calls for synthetic local jobs, "
                "reporting unsafe requests separately from executions. Claims, receipts, and a fixed snapshot "
                "still publish completed attacks. Provenance execution zeros come from a deterministic code "
                "gate while both supervisors still request unsafe publication. "
                f"We replay {mutations_n} summary mutations, document limited paired coverage, null the "
                f"explicit trusted-summary field in {blind_n} new calls, and offer {retry_n} eligible blocked "
                "requests one gate-feedback retry. ")
    if 'wording_primary' in parents:
        abstract += ("A separate corpus removes template wording and adds a one-speaker honest control; "
                     "failed generations remain visible in planned denominators. ")
    abstract += ("Counts, exclusions, and ledger hashes remain inspectable, with models and speaker "
                 "conditions reported separately. Four families cover only three mechanisms, attacks "
                 "are instructed, and the sink is fake. The results support separating decisions from "
                 "enforcement within this catalog; they do not establish general model safety.")
    output = {}
    for venue in ("main", "aaai"):
        folder = ROOT / "paper" if venue == "main" else ROOT / "paper/venues/aaai"
        folder.mkdir(parents=True, exist_ok=True)
        preamble = (r"\documentclass{article}\usepackage{arxiv}" if venue == "main" else
                    r"\documentclass[letterpaper]{article}\usepackage[preprint]{aaai2027}")
        preamble += r"\usepackage[utf8]{inputenc}\usepackage{booktabs,graphicx,amsmath}\usepackage{url,natbib,placeins}"
        if venue == "main":
            preamble += r"\usepackage[hidelinks]{hyperref}"
        affiliation = escape(author.get("affiliation", ""))
        authors = r"\author{" + escape(author["name"]) + (
            r"\\" + affiliation + "}" if venue == "main" else "}\\affiliations{" + affiliation + "}")
        manuscript_body = body
        if venue == "main":
            shutil.copy2(figure, folder / 'seal_measurement.pdf')
            manuscript_body = (r'\begin{figure}[t]\centering\includegraphics[width=\linewidth]{seal_measurement.pdf}' +
                r'\caption{Original unsafe publication requests and executions on completed attacks. Each panel is a separate model and condition; n denotes completed cases. These are catalog point estimates, not population guarantees.}\end{figure}' + '\n' + body)
        source = preamble + "\n\\title{" + title + "}\n" + authors + "\n\\date{}\n" + (
            "\\begin{document}\n\\maketitle\n\\begin{abstract}" + abstract + "\\end{abstract}\n" + manuscript_body + "\\end{document}\n")
        if venue == "main" and not (folder / "legacy_main.tex").exists():
            shutil.copy2(folder / "main.tex", folder / "legacy_main.tex")
            if (folder / 'main.pdf').exists():
                shutil.copy2(folder / 'main.pdf', folder / 'legacy_main.pdf')
        target = folder / f"{venue}.tex"
        target.write_text(source, encoding="utf8")
        if venue == "aaai":
            for name in ("aaai2027.sty", "aaai2027.bst"):
                shutil.copy2(ROOT / "paper/templates/aaai2027" / name, folder / name)
        build = ROOT / "build/venues" / venue
        build.mkdir(parents=True, exist_ok=True)
        if compile_pdf:
            engine = shutil.which("pdflatex")
            if not engine:
                raise RuntimeError("Official AAAI style requires PDFLaTeX; no compatible engine found")
            for _ in range(2):
                result = subprocess.run([engine, "-interaction=nonstopmode", "-halt-on-error",
                                         f"-output-directory={build}", str(target)],
                                        cwd=folder, capture_output=True, text=True, errors="replace")
                if result.returncode:
                    raise RuntimeError(result.stdout[-5000:])
            shutil.copy2(build / f"{venue}.pdf", folder / f"{venue}.pdf")
            import pymupdf
            doc = pymupdf.open(build / f"{venue}.pdf")
            if venue == "aaai":
                # Content through the reference heading must fit in seven pages.
                body_pages = len(doc)
                for i, page in enumerate(doc):
                    text = page.get_text()
                    if 'References' in text:
                        before_references = text.split('References', 1)[0].strip()
                        body_pages = i + bool(before_references)
                        break
                if body_pages > 7:
                    raise ValueError("AAAI body exceeds seven pages")
            for i, page in enumerate(doc):
                page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(build / f"page_{i+1}.png")
            output[venue] = {"pages": len(doc), "pdf": str(folder / f"{venue}.pdf")}
        inputs = {"source": sc.sha256_file(target), "generator": sc.sha256_file(Path(__file__)),
                  "followup_analysis_generator": sc.sha256_file(ROOT / 'scripts/analyze_seal_followups.py'),
                  "original_worker_audit_generator": sc.sha256_file(ROOT / 'scripts/seal_worker_audit.py'),
                  "baseline": sc.sha256_file(ROOT / "build/seal/analysis/two_endpoints.csv"),
                  "pairing": sc.sha256_file(ROOT / 'build/seal/analysis/pairing_coverage.csv'),
                  "mutations": sc.sha256_file(ROOT / 'build/gate_mutations_manifest.json'),
                  "blind_comparison": sc.sha256_file(ROOT / "build/seal/analysis/provenance_comparison.csv"),
                  "retry": sc.sha256_file(ROOT / "build/seal/analysis/retry_endpoints.csv")}
        if 'wording_primary' in parents:
            for key, name in (('wording', 'wording_endpoints.csv'), ('wording_audit', 'wording_audit.json'),
                              ('diagnostic', 'wording_diagnostic_endpoints.csv')):
                inputs[key] = sc.sha256_file(ROOT / 'build/seal/analysis' / name)
            inputs['cohort_selection'] = sc.sha256_file(ROOT / 'data/wording_cohort_selection.json')
        follow.write_json(build / "evidence_manifest.json", {"venue": venue, "parents": parents,
                          "input_sha256": inputs, "output": output.get(venue)})
    return output


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
