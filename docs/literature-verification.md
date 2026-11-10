# Literature Verification and Primary Source Audit

**Date of Audit:** 10 November 2026
**Scope:** Verification of literature citations in `docs/literature-matrix.md` and `paper/live_body.tex` for `G:/swarm-alignment-2026-sprint`.
**Auditor:** Independent Research Sub-Agent (SEAL Sprint)
**Verification Method:** Independent audit of public primary sources against arXiv, ACM Digital Library, NeurIPS, ICML, and Crossref DOI registries.

---

## 1. Executive Summary

This independent literature verification audits all 16 distinct citations appearing across `docs/literature-matrix.md` and `paper/live_body.tex`.

### Key Findings
1. **Primary Source Authenticity:** All 16 primary sources—including the six 2026 arXiv preprints—were verified against public primary records. None of the citations are fabricated.
2. **Precedent Coverage:** Foundational literature spanning active feature acquisition, selective prediction, adversarial missingness, secure logging, and distribution-free risk calibration is verified. The claimed relevance in `docs/literature-matrix.md` accurately identifies that the mechanisms proposed for future budgeted evidence recovery have extensive, rigorous precedents.
3. **Manuscript Omissions in `paper/live_body.tex`:**
   - **Missing Key Citations:** Two foundational bibliographic anchors present in `docs/literature-matrix.md`—Globerson & Roweis (ICML 2006, adversarial missingness) and Schneier & Kelsey (ACM TISSEC 1999, secure audit logs)—are mentioned conceptually in the text but have **no `\bibitem` entries** in `paper/live_body.tex`.
   - **Missing URLs:** 13 of the 14 `\bibitem` entries in `paper/live_body.tex` lack direct `\url{...}` links (only `debenedetti2024agentdojo` contains an explicit URL).
4. **Publication Venue Updates:** Multiple entries cited strictly as arXiv preprints have been formally published in peer-reviewed archival venues (e.g., Greenblatt et al. at ICML 2024, Ma et al. at ICML 2019, Geifman & El-Yaniv at NeurIPS 2017, Debenedetti et al. at NeurIPS 2024).
5. **Matrix vs. Manuscript Asymmetry:** Four citations present in `paper/live_body.tex` (`wallace2024instructionhierarchy`, `debenedetti2024agentdojo`, `debenedetti2025camel`, and `shi2026emergentcollusion`) are omitted from `docs/literature-matrix.md`. While the matrix specifically targets the prospective budgeted recovery protocol, cross-referencing background references is recommended.

---

## 2. Core Methodological Precedents (Deep-Dive)

### 2.1 Active Acquisition
* **Canonical Reference:** Chao Ma, Sebastian Tschiatschek, Konstantina Palla, José Miguel Hernández-Lobato, Sebastian Nowozin, Cheng Zhang. *EDDI: Efficient Dynamic Discovery of High-Value Information with Partial VAE*. ICML 2019 / arXiv:1809.11142 (September 2018).
* **Verified URL:** [https://arxiv.org/abs/1809.11142](https://arxiv.org/abs/1809.11142)
* **Methodological Precedent:** Formulates Active Feature Acquisition (AFA) under a Partial Variational Autoencoder (p-VAE) with a permutation-invariant encoder capable of handling arbitrary subsets of observed features. It uses an information-theoretic policy (expected information gain / mutual information between unobserved features and the target variable) to choose features dynamically per step.
* **Relevance to SEAL / Extension:** The SEAL project's proposed extension aims to dynamically recover authenticated missing evidence records. EDDI establishes that selecting missing features to maximize information gain under acquisition costs is an established paradigm. SEAL cannot claim novelty for the concept of cost-sensitive information acquisition; any contribution is limited to applying policy-aware recovery on cryptographically authenticated, tampered/omitted audit logs.

### 2.2 Selective Prediction (Reject Option)
* **Canonical Reference:** Yonatan Geifman and Ran El-Yaniv. *Selective Classification for Deep Neural Networks*. NeurIPS 2017 / arXiv:1705.08500 (May 2017).
* **Verified URL:** [https://arxiv.org/abs/1705.08500](https://arxiv.org/abs/1705.08500)
* **Methodological Precedent:** Introduces a formal framework for selective classification (prediction with a reject/abstain option) in deep learning. Given a user-defined risk tolerance $\theta$ and confidence $\delta$, it bounds the selective risk on non-rejected instances below $\theta$ with probability $1 - \delta$ by thresholding softmax response/confidence over a validation set, quantifying the trade-off with data coverage.
* **Relevance to SEAL / Extension:** SEAL evaluates supervisory decisions categorized as `block`, `abstain`, and `publish`. Geifman & El-Yaniv demonstrate that trading task coverage for reduced operational risk is established. SEAL's current release produces descriptive outcomes without finite-sample selective risk bounds; a prospective extension must rigorously frame held legitimate tasks as lost coverage against selective risk.

### 2.3 Adversarial Missingness and Robust Classification
* **Canonical Reference:** Amir Globerson and Sam T. Roweis. *Nightmare at test time: robust learning by feature deletion*. ICML 2006, pp. 353–360.
* **Verified DOI:** [https://doi.org/10.1145/1143844.1143889](https://doi.org/10.1145/1143844.1143889)
* **Methodological Precedent:** Formulates classification under worst-case feature deletion at test time as a zero-sum minimax game between a learner and an adversary deleting up to $K$ features. Converts the combinatorial deletion problem into a convex quadratic program (FDROP) for linear models under hinge loss.
* **Relevance to SEAL / Extension:** Crucial bibliographic foundation for adversarial omission and deliberate feature hiding. In SEAL, an adversary selectively hides audit records or commits inconsistent off-keeper states. Globerson & Roweis provide the theoretical foundation for worst-case evaluation against feature suppression.

### 2.4 Secure Logging and Forensic Integrity
* **Canonical Reference:** Bruce Schneier and John Kelsey. *Secure audit logs to support computer forensics*. ACM Transactions on Information and System Security (TISSEC), Vol. 2, No. 2, May 1999, pp. 159–176. Precursor in 6th USENIX Security Symposium (1998).
* **Verified DOI:** [https://doi.org/10.1145/317087.317089](https://doi.org/10.1145/317087.317089)
* **Methodological Precedent:** Establishes cryptographic protocols for audit logs created on untrusted or potentially compromised machines, ensuring forward integrity (tamper-evident audit trails using evolving keys and one-way hash chains such that retrospective modification or deletion of prior logs is detected upon verification with a trusted verifier).
* **Relevance to SEAL / Extension:** Foundational precedent for SEAL's Ed25519 signatures, digest manifests, and provenance tracking. SEAL's current demonstration keys are deterministic local demo artifacts; Schneier & Kelsey define the trust boundary requirements necessary to claim real forensic audit security under adversarial machine compromise.

### 2.5 Distribution-Free Risk Calibration
* **Canonical Reference 1 (Learn then Test):** Anastasios N. Angelopoulos, Stephen Bates, Emmanuel J. Candès, Michael I. Jordan, Lihua Lei. *Learn then Test: Calibrating Predictive Algorithms to Achieve Risk Control*. arXiv:2110.01052 (October 2021).
  * **Verified URL:** [https://arxiv.org/abs/2110.01052](https://arxiv.org/abs/2110.01052)
  * **Methodological Precedent:** Generalizes conformal calibration by framing risk control across candidate configurations as multiple hypothesis testing. Provides finite-sample risk control with probability $1 - \delta$ on arbitrary, non-monotone loss functions (e.g., FDR, IoU) using concentration bounds and multiple testing corrections.
* **Canonical Reference 2 (Conformal Risk Control):** Anastasios N. Angelopoulos, Stephen Bates, Adam Fisch, Lihua Lei, Tal Schuster. *Conformal Risk Control*. arXiv:2208.02814 (August 2022).
  * **Verified URL:** [https://arxiv.org/abs/2208.02814](https://arxiv.org/abs/2208.02814)
  * **Methodological Precedent:** Extends conformal prediction to bound the expected value of any monotone loss function $\mathbb{E}[\ell(X_{n+1}, Y_{n+1}, \hat{\lambda})] \le \alpha$ under exchangeability, without requiring distributional assumptions.
* **Relevance to SEAL / Extension:** SEAL reports descriptive 2,000-replicate family-cluster bootstrap percentile intervals over four family blocks. As accurately identified in `literature-matrix.md`, bootstrap intervals do not constitute distribution-free statistical risk guarantees. Any future stopping/recovery policy claiming risk control must satisfy exchangeability and formal multiple testing or monotone loss calibration theorems.

---

## 3. Comprehensive Reference Verification Table

The table below catalogs every reference cited across `docs/literature-matrix.md` and `paper/live_body.tex`:

| Ref / Key | Authors | Formal Title | Venue / Date | Verified Primary URL | Matrix | Paper | Verification Status |
|---|---|---|---|---|---|---|---|
| `globerson2006nightmare` | Amir Globerson, Sam T. Roweis | *Nightmare at test time: robust learning by feature deletion* | ICML 2006 (pp. 353–360) | [https://doi.org/10.1145/1143844.1143889](https://doi.org/10.1145/1143844.1143889) | Yes | In text (no bib) | **Verified** (Primary DOI). Missing `\bibitem` in `live_body.tex`. |
| `schneier1999secure` | Bruce Schneier, John Kelsey | *Secure audit logs to support computer forensics* | ACM TISSEC Vol. 2 No. 2, May 1999 (pp. 159–176) | [https://doi.org/10.1145/317087.317089](https://doi.org/10.1145/317087.317089) | Yes | In text (no bib) | **Verified** (Primary DOI). Missing `\bibitem` in `live_body.tex`. |
| `ma2018eddi` | Chao Ma, Sebastian Tschiatschek, Konstantina Palla, José Miguel Hernández-Lobato, Sebastian Nowozin, Cheng Zhang | *EDDI: Efficient Dynamic Discovery of High-Value Information with Partial VAE* | ICML 2019 / arXiv:1809.11142 (Sep 2018) | [https://arxiv.org/abs/1809.11142](https://arxiv.org/abs/1809.11142) | Yes | Yes | **Verified**. Published in ICML 2019. Missing URL in `live_body.tex`. |
| `geifman2017selective` | Yonatan Geifman, Ran El-Yaniv | *Selective Classification for Deep Neural Networks* | NeurIPS 2017 / arXiv:1705.08500 (May 2017) | [https://arxiv.org/abs/1705.08500](https://arxiv.org/abs/1705.08500) | Yes | Yes | **Verified**. Published in NeurIPS 2017. Missing URL in `live_body.tex`. |
| `angelopoulos2021ltt` | Anastasios N. Angelopoulos, Stephen Bates, Emmanuel J. Candès, Michael I. Jordan, Lihua Lei | *Learn then Test: Calibrating Predictive Algorithms to Achieve Risk Control* | arXiv:2110.01052 (Oct 2021) | [https://arxiv.org/abs/2110.01052](https://arxiv.org/abs/2110.01052) | Yes | Yes | **Verified**. Missing URL in `live_body.tex`. |
| `angelopoulos2022crc` | Anastasios N. Angelopoulos, Stephen Bates, Adam Fisch, Lihua Lei, Tal Schuster | *Conformal Risk Control* | arXiv:2208.02814 (Aug 2022) | [https://arxiv.org/abs/2208.02814](https://arxiv.org/abs/2208.02814) | Yes | Yes | **Verified**. Missing URL in `live_body.tex`. |
| `greenblatt2023aicontrol` | Ryan Greenblatt, Buck Shlegeris, Kshitij Sachan, Fabien Roger | *AI Control: Improving Safety Despite Intentional Subversion* | ICML 2024 / arXiv:2312.06942 (Dec 2023) | [https://arxiv.org/abs/2312.06942](https://arxiv.org/abs/2312.06942) | Yes | Yes | **Verified**. Published in ICML 2024. Missing URL in `live_body.tex`. |
| `wallace2024instructionhierarchy` | Eric Wallace, Kai Xiao, Reimar Leike, Lilian Weng, Johannes Heidecke, Alex Beutel | *The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions* | arXiv:2404.13208 (Apr 2024) | [https://arxiv.org/abs/2404.13208](https://arxiv.org/abs/2404.13208) | No | Yes | **Verified**. Missing URL in `live_body.tex`. Omitted from matrix. |
| `debenedetti2024agentdojo` | Edoardo Debenedetti, Jie Zhang, Mislav Balunović, Luca Beurer-Kellner, Marc Fischer, Florian Tramèr | *AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents* | NeurIPS 2024 (Datasets & Benchmarks) / arXiv:2406.13352 (Jun 2024) | [https://arxiv.org/abs/2406.13352](https://arxiv.org/abs/2406.13352) | No | Yes | **Verified**. Published in NeurIPS 2024. Contains URL in `live_body.tex`. Omitted from matrix. |
| `debenedetti2025camel` | Edoardo Debenedetti, Ilia Shumailov, Tianqi Fan, Jamie Hayes, Nicholas Carlini, Daniel Fabian, Christoph Kern, Chongyang Shi, Andreas Terzis, Florian Tramèr | *Defeating Prompt Injections by Design* | arXiv:2503.18813 (Mar 2025) | [https://arxiv.org/abs/2503.18813](https://arxiv.org/abs/2503.18813) | No | Yes | **Verified**. Missing URL in `live_body.tex`. Omitted from matrix. |
| `rose2026helios` | Aaron Rose, Carissa Cullen, Sahar Abdelnabi, Philip Torr, Brandon Gary Kaplowitz, Christian Schroeder de Witt | *Detecting Multi-Agent Collusion Through Multi-Agent Interpretability* | arXiv:2604.01151 (Apr 2026) | [https://arxiv.org/abs/2604.01151](https://arxiv.org/abs/2604.01151) | Yes | Yes | **Verified**. Missing URL in `live_body.tex`. |
| `shi2026emergentcollusion` | Xinrui Shi, Yanzhe Zhang, Diyi Yang | *Emergent Collusion in Long-Horizon LLM Agent Interaction* | arXiv:2609.24967 (Sep 2026) | [https://arxiv.org/abs/2609.24967](https://arxiv.org/abs/2609.24967) | No | Yes | **Verified**. Missing URL in `live_body.tex`. Omitted from matrix. |
| `mohsin2026probes` | Mainul Hasan Mohsin, Mohammad Shahidur Rahman | *What Do Collusion Probes Read? Three Pitfalls in Evaluating Multi-Agent Collusion Monitors* | arXiv:2611.10620 (Nov 2026) | [https://arxiv.org/abs/2611.10620](https://arxiv.org/abs/2611.10620) | Yes | Yes | **Verified**. Missing URL in `live_body.tex`. |
| `raghav2026precrime` | Leonidas Raghav | *Pre-Crime for Agents, at the Last Second: Probing When a Recruited Agent Gives In* | arXiv:2610.40588 (Oct 2026) | [https://arxiv.org/abs/2610.40588](https://arxiv.org/abs/2610.40588) | Yes | Yes | **Verified**. Author is Leonidas Raghav. Missing URL in `live_body.tex`. |
| `pinto2026calibration` | Lucas Pinto | *Calibration-Family Overfit: Why Trusted Sabotage Monitors Don't Transfer Across Lineages* | COLM 2026 Workshop / arXiv:2607.06596 (Jul 2026) | [https://arxiv.org/abs/2607.06596](https://arxiv.org/abs/2607.06596) | Yes | Yes | **Verified**. Author is Lucas Pinto. Missing URL in `live_body.tex`. |
| `gewang2026selection` | Catherine Ge-Wang, Tyler Crosse, Benjamin Hadad IV, Joachim Schaeffer, Ram Potham, Tyler Tracy | *Attack Selection in Agentic AI Control Evaluations Meaningfully Decreases Safety* | arXiv:2606.06529 (Jun 2026) | [https://arxiv.org/abs/2606.06529](https://arxiv.org/abs/2606.06529) | Yes | Yes | **Verified**. Missing URL in `live_body.tex`. |

---

## 4. Verification of 2026 arXiv Records

Because 2026 literature reflects recent and emerging work in multi-agent safety and AI control, each of the six 2026 preprints was subjected to independent bibliographic and technical verification:

1. **Rose et al. (arXiv:2604.01151, April 2026)**
   * *Title:* Detecting Multi-Agent Collusion Through Multi-Agent Interpretability
   * *Lead Authors:* Aaron Rose, Carissa Cullen (Joint first authors; University of Oxford)
   * *Technical Focus:* Introduces the HELIOS framework combining per-agent activation probes with composite hypothesis testing, and the NARCBench benchmark on open-weight models (Qwen3-32B, Llama-3.1-70B, DeepSeek-R1 32B).
   * *Relevance Check:* Accurately described in `literature-matrix.md` and `live_body.tex` as white-box activation monitoring for collusion. SEAL operates strictly in an external transcript/evidence-audit regime without model activation access.

2. **Shi, Zhang, and Yang (arXiv:2609.24967, September 2026)**
   * *Title:* Emergent Collusion in Long-Horizon LLM Agent Interaction
   * *Lead Authors:* Xinrui Shi, Yanzhe Zhang, Diyi Yang (Stanford University)
   * *Technical Focus:* Studies spontaneous collusion emerging without adversarial prompting when agents repeatedly verify each other over extended interaction horizons (emerged in 94% of trajectories across 10 LLM families).
   * *Relevance Check:* Accurately cited in `live_body.tex` Section 2 as a contrast to SEAL's methodology. SEAL's workers execute fixed scripted instructions rather than emergent long-horizon coordination.

3. **Mohsin and Rahman (arXiv:2611.10620, November 2026)**
   * *Title:* What Do Collusion Probes Read? Three Pitfalls in Evaluating Multi-Agent Collusion Monitors
   * *Lead Authors:* Mainul Hasan Mohsin, Mohammad Shahidur Rahman (Shahjalal University of Science and Technology)
   * *Technical Focus:* Exposes three pitfalls in multi-agent collusion probes: (1) instruction presence (probes firing on briefing text rather than actual decisions, tested with briefed refusers), (2) post-decision outcome leakage, and (3) public-record ceiling (colluders concealing arguments on public channels).
   * *Relevance Check:* Accurately cited in `literature-matrix.md` and `live_body.tex`. Direct relevance to SEAL: SEAL uses scripted instructed claims and honest controls, without live briefed-refusal or uninstructed interaction populations.

4. **Raghav (arXiv:2610.40588, October 2026)**
   * *Title:* Pre-Crime for Agents, at the Last Second: Probing When a Recruited Agent Gives In
   * *Lead Author:* Leonidas Raghav
   * *Technical Focus:* Probes internal activations of agents undergoing peer pressure right before rule violation; shows warning signals trigger immediately before disclosure but cause high benign disruption on authorized disclosures.
   * *Relevance Check:* Accurately cited. Highlights the danger of naive early-stopping and alarm policies disrupting benign execution.

5. **Pinto (arXiv:2607.06596, July 2026)**
   * *Title:* Calibration-Family Overfit: Why Trusted Sabotage Monitors Don't Transfer Across Lineages
   * *Lead Author:* Lucas Pinto (COLM 2026 Workshop on AI Measurement Science)
   * *Technical Focus:* Identifies an own-family interaction advantage (+0.172 AUROC) where sabotage monitors fitted on one model family fail to transfer to untrusted models from another family under a 1% audit budget.
   * *Relevance Check:* Directly supports SEAL's decision to report `gemma-4-31b-it` and `qwen3.5-397b` separately without pooling, and warns against assuming monitor transferability across model lineages.

6. **Ge-Wang et al. (arXiv:2606.06529, June 2026)**
   * *Title:* Attack Selection in Agentic AI Control Evaluations Meaningfully Decreases Safety
   * *Lead Authors:* Catherine Ge-Wang, Tyler Crosse, Benjamin Hadad IV, Joachim Schaeffer, Ram Potham, Tyler Tracy
   * *Technical Focus:* Evaluates opportunistic attack timing (strategic start and stop policies) in agentic control benchmarks (BashArena, LinuxArena) at limited human audit budgets, showing safety decreases by 20–28 percentage points compared to uniform attacks.
   * *Relevance Check:* Accurately cited in `literature-matrix.md` and `live_body.tex`. SEAL uses a fixed attack catalog and schedule; Ge-Wang et al. emphasizes that adaptive attackers can exploit fixed monitoring policies.

---

## 5. What Could Not Be Verified / Discrepancies Identified

1. **Unanchored Seminal Works in Manuscript:**
   * In `paper/live_body.tex` (Line 11), the text states: *"Active acquisition and selective classification already study information cost and rejection coverage \cite{ma2018eddi,geifman2017selective}; risk-control methods require calibration assumptions beyond descriptive bootstrap intervals \cite{angelopoulos2021ltt,angelopoulos2022crc}."*
   * However, **Globerson & Roweis (2006)** (adversarial missingness) and **Schneier & Kelsey (1999)** (secure audit logs) are mentioned prominently in `docs/literature-matrix.md` as core bibliographic anchors, but are **completely omitted from `paper/live_body.tex` `\begin{thebibliography}`**.
   * *Verification Status:* The papers themselves are verified, but their citation status in the manuscript is incomplete.

2. **Missing Hyperlinks in `paper/live_body.tex`:**
   * In `paper/live_body.tex`, only `\bibitem{debenedetti2024agentdojo}` has a `\url{https://arxiv.org/abs/2406.13352}` macro.
   * The remaining 13 bibliography entries omit URLs, leaving reader verification incomplete within the compiled PDF.

3. **Incomplete Author Nomenclature in Prose:**
   * In `docs/literature-matrix.md`, single-name or informal shorthand is used for certain records:
     - "Raghav" instead of "Leonidas Raghav" or "L. Raghav".
     - "Pinto" instead of "Lucas Pinto" or "L. Pinto".
     - "Mohsin and Rahman" instead of "M. H. Mohsin and M. S. Rahman".
     - "Ge-Wang et al." (omits first author's full name Catherine Ge-Wang).

4. **Archival Publication Venue Omission:**
   * Greenblatt et al. (2023) is listed solely as arXiv:2312.06942, but was published in the *Proceedings of the 41st International Conference on Machine Learning (ICML 2024)*.
   * Ma et al. (2018) was published in *ICML 2019*.
   * Geifman & El-Yaniv (2017) was published in *NeurIPS 2017*.
   * Debenedetti et al. (2024) was published in *NeurIPS 2024*.

---

## 6. Recommended Corrections

To maintain rigorous bibliographic integrity without fabricating novelty or sources, the following corrections are recommended when the manuscript and documentation are next revised:

### 6.1 Recommendations for `paper/live_body.tex`
1. **Add `\bibitem` entries for Globerson & Roweis (2006) and Schneier & Kelsey (1999):**
   ```latex
   \bibitem{globerson2006nightmare}
   A. Globerson and S. T. Roweis.
   \newblock Nightmare at test time: robust learning by feature deletion.
   \newblock In \emph{Proceedings of the 23rd International Conference on Machine Learning (ICML)}, pp. 353--360, 2006.
   \url{https://doi.org/10.1145/1143844.1143889}.

   \bibitem{schneier1999secure}
   B. Schneier and J. Kelsey.
   \newblock Secure audit logs to support computer forensics.
   \newblock \emph{ACM Transactions on Information and System Security (TISSEC)}, 2(2):159--176, 1999.
   \url{https://doi.org/10.1145/317087.317089}.
   ```
2. **Add Verified Direct URLs to all `\bibitem` entries in `paper/live_body.tex`:**
   Ensure each entry includes its canonical `\url{https://arxiv.org/abs/...}` or `\url{https://doi.org/...}` link.
3. **Update Venues:** Update Greenblatt et al. to cite ICML 2024; Ma et al. to cite ICML 2019; Geifman & El-Yaniv to cite NeurIPS 2017; Debenedetti et al. to cite NeurIPS 2024.

### 6.2 Recommendations for `docs/literature-matrix.md`
1. **Align Background Benchmarks:** Add a supplemental section or note in `docs/literature-matrix.md` covering the background control and prompt-injection benchmarks referenced in the paper introduction (`AgentDojo`, `Instruction Hierarchy`, `CaMeL`, and `Emergent Collusion`), clarifying their relationship to the prospective budgeted recovery protocol.
2. **Standardize Author Initials:** Use full initialed surnames (e.g., "L. Raghav (2026)", "L. Pinto (2026)", "M. H. Mohsin and M. S. Rahman (2026)") to prevent ambiguity.

---

## 7. Conclusion

All 16 cited literature records are authentic, publicly verifiable primary sources. The claimed relevance of each work in both `docs/literature-matrix.md` and `paper/live_body.tex` is technically grounded. The foundational literature on active acquisition, selective classification, feature deletion, tamper-evident audit logs, and risk control is established in prior art; the project appropriately documents that prospective budgeted evidence recovery builds upon these precedents rather than claiming ungrounded novelty.
