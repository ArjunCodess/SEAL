# Workflow context measurement

Cost-only exceedance screen using a **local pinned tokenizer**, not provider usage.

## Freeze
- Manifest SHA-256: `3e545f7aabcad1c8ce4267ee2cae97242a447f5fe7e4d16deff23fdb4549e3a0`
- Freeze digest: `c58a7355910a5f497eadcf8b7184769d61b26a2f4dccba607e556a64bd83c71a`
- Tokenizer spec digest: `472aa80492ed0ce73418d74eb3c8cef841300e4edf0f10e137b68e42ffe021c8`
- Tokenizer status: `loaded_local`

## Context constraint (Qwen pin)
- Context window: 131072 tokens
- Output reserve: 8192 tokens
- Max input tokens: **122880**

## Go rule
- Target lower bound on exceedance fraction: **0.10**
- One-sided confidence: 0.95
- Exceedance successes / groups: 1 / 30
- Clopper–Pearson lower bound: 0.001708315644465736
- Decision: **no_go** (go=False)

## Cost estimate (input only)
- Pinned input price: 0.55 credits / million tokens (not measured spend).

## Caveats
- Token counts use a locally pinned Hugging Face tokenizer (or test adapter), not provider billing tokenizers.
- Byte lengths are UTF-8 octets of the extracted transcript text; bytes are not substituted for tokens.
- Input credit estimates apply the pinned input price only; they are not measured API spend.
- The context go rule is cost-only and independent of recovery unsafe-release planning targets.
- Unknown token counts are treated as non-exceedance for the exceedance-fraction bound (conservative).
- Failed ingest units remain in the exceedance denominator.
