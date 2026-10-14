# Research protocol template

Fill this in inside your own experiment repository, created from the boilerplate.
Keep experiment-specific definitions and results in that fork, not in the template.

Complete this before collecting the research results. Preserve later amendments
as new versions and identify analyses developed after inspecting outputs.

## Question and scope

- Research question and intended claim:
- Population the dataset represents:
- Explicit limits on generalization:
- Primary scoring unit (example, finding, test, or another entity):
- Correlated groups (repository, original file, participant, etc.):

## Experimental conditions

- Dataset/source revision and inclusion/exclusion criteria:
- Sampling procedure, seed, and sample size:
- Workload packaging, order, and supplied context:
- Prompt and response-schema revisions:
- Harness, exact observed CLI version, model, and model-specific options:
- Available tools, network access, instructions, and environment:
- Per-session timeout, concurrency, and number of independent replicates:
- Factor(s) varied and factors held constant:

Preserve stable sample IDs across conditions. A new batch arrangement is a new
workload, not a new sample. A retry is operational recovery, not another replicate.
Seeds control local sampling/packaging; they do not make remote models deterministic.

## Reference and evaluation

- Reference origin, version, and known incompleteness:
- Primary evaluator, score definition, and denominator:
- Treatment of failures, partial coverage, unknown labels, and abstentions:
- Matching/adjudication policy where exact equality is inappropriate:
- Human/assistant reviewer identity and independence, if applicable:
- Secondary and exploratory analyses:

The supplied evaluator is strict JSON exact match. Its end-to-end denominator is
all selected samples with reference answers, with zero credit for missing
predictions. Its conditional score uses only valid answered reference samples.
An omitted reference is unknown; an explicit JSON null is a real reference value.
For open-ended findings, implement and freeze an appropriate matching policy;
absence of a reference finding does not automatically establish a false positive.

## Operational conventions

Prepared bundles are immutable. Configuration or code changes require new runs.
Retries are explicit and retain all attempts. The collector selects the latest
valid attempt per workload; it never merges responses from different attempts.
Incomplete valid responses remain incomplete and are not automatically retried.
All attempts contribute to resource accounting. Missing usage stays unknown.

Report full coverage, exclusions, all failures, and descriptive paired results.
Account for grouping and repeated observations before adding uncertainty estimates.
The included comparison does not perform significance tests.
