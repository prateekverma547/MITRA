# Contract fixtures

Sample instances of the contracts in `shared/`, for building screens and tests
before the real producer exists. Every file here is validated against its
contract by `tests/hiring/test_contracts.py`, so a fixture can never drift from
the shape it claims to be.

| File | Contract | Producer |
| --- | --- | --- |
| `fit_analysis.json` | `shared.screening.FitAnalysis` | Track H (`analyse_fit`) |
| `hr_questions.json` | `shared.screening.HRQuestionSet` | Track H (`hr_questions`) |
| `hr_round_evaluation.json` | `shared.screening.HRRoundEvaluation` | Track H (`evaluate_hr_round`) |
| `decision.json` | `shared.stages.Decision` | Track H (`move`) |

The fit analysis scores `tests/fixtures/documents/cv_karandeep_aiml.txt` against
`JD-C-senior-aiml-engineer.md`; every CV quote in it appears in that CV. The HR
round quotes are illustrative: there is no recorded HR round behind them.
