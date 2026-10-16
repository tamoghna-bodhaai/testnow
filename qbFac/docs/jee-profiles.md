# JEE item profiles

qbFac is initially scoped to the PCM portion of JEE Main Paper 1 and JEE
Advanced. A profile is a delivery target, not a claim that the same question
will appear in an official examination.

## JEE Main Paper 1

Use `jee-main-paper-1` for:

- `single_choice`: one selected option;
- `numerical`: a numerical-value answer with any accepted tolerance explicitly
  stored in the interaction contract;
- `integer`: a constrained numerical answer when the source specifies an
  integer.

The 2026 JEE Main Information Bulletin is the source of truth for the current
paper pattern and can change between sessions. qbFac stores the rule set on an
item rather than hard-coding a year's marks in its type.

## JEE Advanced

Use `jee-advanced` for:

- `single_choice` (MCQ);
- `multiple_select` (MSQ), including partial-credit rules;
- `numerical` (NAT);
- `matching_list` (MLT).

The exact mix and marking scheme are paper/year-specific. JEE Advanced 2025's
official Joint Implementation Committee report demonstrates all four types and
partial-credit MSQ scoring; the official 2026 brochure confirms CBT delivery
with option and numeric response modes.

For a partially scored MSQ, qbFac requires explicit `scoring.rules`, for
example `[{"selected":["A", "C"], "score":4}, {"selected":["A"], "score":1}]`.
The release validator checks that each rule refers to declared, unique option
IDs. The consuming test builder decides which year/paper blueprint uses it.

## Source links

- JEE Main, [2026 Information Bulletin](https://jeemain.nta.nic.in/information-bulletin/)
- JEE Advanced, [2026 Information Brochure](https://jeeadv.ac.in/documents/IBEnglish_2026.pdf)
- JEE Advanced, [2025 Joint Implementation Committee Report](https://jeeadv.ac.in/reports/2025.pdf)

Other source labels such as assertion-reason and passage/comprehension remain
valid qbFac item kinds. They must be mapped to an explicit delivery profile by
the consuming exam system rather than silently coerced to MCQ.
