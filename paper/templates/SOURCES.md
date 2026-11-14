# Manuscript formats

The main preprint uses George Kour's arXiv style linked by the user at
https://www.overleaf.com/latex/templates/style-and-template-for-preprints-arxiv-bio-arxiv/fxsnsrzpnvwc .
`paper/arxiv.sty` was compared byte for byte with the linked upstream
`https://raw.githubusercontent.com/kourgeorge/arxiv-style/master/arxiv.sty` on
13 November 2026. Both SHA-256 hashes are
`5ca551dbda41573265dd8c1e9c983ba9729f7d4070a9f1503dd8c7fd65d1dc46`.

AGENTVERIFY uses unmodified IEEEtran with explicit `10pt,conference` options,
as required by ICSE 2027. The class is preserved under `ieee/`, with its hash
and format source in `ieee/source.json`. AGENTVERIFY's own CFP requires an
anonymous IEEE/ICSE extended abstract of two pages including references:
https://agentverify2027.github.io/cfp.html .

ADMIT requests a one-page PDF presentation proposal and explicitly requires
no template. Its current CFP is https://admit-workshop.github.io/ . This
supersedes the original approximate two-page planning assumption.

AAAI uses the unmodified `aaai2027.sty` and bibliography style extracted from
the official author kit at https://aaai.org/authorkit27/ . The archive hash is
in `aaai2027/source.json`. This style requires PDFLaTeX; the installed MiKTeX
PDFLaTeX engine passed a local compiler check. The workshop uses single-blind
review and a maximum of seven content pages, excluding references.

A³ has no manuscript or template artifact yet because its call has not opened.
