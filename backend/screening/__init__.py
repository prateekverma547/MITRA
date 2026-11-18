"""CV screening: a CV against a job, before any interview. Track H.

`redact` removes what a screener must not see, `fit` asks the model for
ratings and checks every quote, and the percentage is computed in
`shared.screening.compute_fit`, never by the model.
"""
