# Person B Final Context Benchmark
Generated at: 2026-09-26T21:53:42.088783+00:00

Compared:
- Static Context
- Adaptive Context
- Adaptive + Artifact Memory

## Aggregate Metrics

| Metric | Static | Adaptive | Adaptive + Artifact |
|---|---:|---:|---:|
| Average Estimated Tokens (Final) | 453.0 | 189.6 | 189.4 |
| Average Estimated Tokens (Before) | 453.0 | 221.3 | 221.1 |
| Average Relevant File Recall | 88.1% | 84.5% | 84.5% |
| Total Irrelevant Files | 5 | 5 | 5 |
| Critical Evidence Retention | 85.7% | 100.0% | 100.0% |
| Budget Violations | 0 | 0 | 0 |

## Scenario Breakdown

### Scenario 1: Normal execution
| Metric | Static | Adaptive | Adaptive + Artifact |
|---|---:|---:|---:|
| Estimated tokens (Final) | 199 | 197 | 197 |
| Estimated tokens (Before)| 199 | 197 | 197 |
| Relevant file recall | 100.0% | 100.0% | 100.0% |
| Irrelevant files | 1 | 1 | 1 |
| Current error retained | True | True | True |
| Failed verification retained | True | True | True |
| Budget respected | True | True | True |


### Scenario 2: First failure
| Metric | Static | Adaptive | Adaptive + Artifact |
|---|---:|---:|---:|
| Estimated tokens (Final) | 203 | 201 | 201 |
| Estimated tokens (Before)| 203 | 201 | 201 |
| Relevant file recall | 100.0% | 100.0% | 100.0% |
| Irrelevant files | 1 | 1 | 1 |
| Current error retained | True | True | True |
| Failed verification retained | True | True | True |
| Budget respected | True | True | True |


### Scenario 3: Repeated failure
| Metric | Static | Adaptive | Adaptive + Artifact |
|---|---:|---:|---:|
| Estimated tokens (Final) | 220 | 218 | 218 |
| Estimated tokens (Before)| 220 | 218 | 218 |
| Relevant file recall | 75.0% | 75.0% | 75.0% |
| Irrelevant files | 0 | 0 | 0 |
| Current error retained | True | True | True |
| Failed verification retained | True | True | True |
| Budget respected | True | True | True |


### Scenario 4: Recovery contraction
| Metric | Static | Adaptive | Adaptive + Artifact |
|---|---:|---:|---:|
| Estimated tokens (Final) | 224 | 222 | 222 |
| Estimated tokens (Before)| 224 | 222 | 222 |
| Relevant file recall | 100.0% | 100.0% | 100.0% |
| Irrelevant files | 1 | 1 | 1 |
| Current error retained | True | True | True |
| Failed verification retained | True | True | True |
| Budget respected | True | True | True |


### Scenario 5: Tight token budget
| Metric | Static | Adaptive | Adaptive + Artifact |
|---|---:|---:|---:|
| Estimated tokens (Final) | 150 | 135 | 135 |
| Estimated tokens (Before)| 150 | 357 | 357 |
| Relevant file recall | 75.0% | 50.0% | 50.0% |
| Irrelevant files | 0 | 0 | 0 |
| Current error retained | False | True | True |
| Failed verification retained | False | True | True |
| Budget respected | True | True | True |


### Scenario 6: Noisy repository
| Metric | Static | Adaptive | Adaptive + Artifact |
|---|---:|---:|---:|
| Estimated tokens (Final) | 175 | 168 | 168 |
| Estimated tokens (Before)| 175 | 168 | 168 |
| Relevant file recall | 66.7% | 66.7% | 66.7% |
| Irrelevant files | 0 | 0 | 0 |
| Current error retained | True | True | True |
| Failed verification retained | True | True | True |
| Budget respected | True | True | True |


### Scenario 7: Large tool output
| Metric | Static | Adaptive | Adaptive + Artifact |
|---|---:|---:|---:|
| Estimated tokens (Final) | 2000 | 186 | 185 |
| Estimated tokens (Before)| 2000 | 186 | 185 |
| Relevant file recall | 100.0% | 100.0% | 100.0% |
| Irrelevant files | 2 | 2 | 2 |
| Current error retained | True | True | True |
| Failed verification retained | True | True | True |
| Budget respected | True | True | True |

