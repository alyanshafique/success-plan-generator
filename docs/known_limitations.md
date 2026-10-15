# Known limitations

The README lists these briefly. Observed failures and their history are in [evaluation_log.md](evaluation_log.md);
evaluation caveats are in [evaluation.md](evaluation.md); the heuristic flags and their limits are in
[how_it_works.md](how_it_works.md).

## Scope and evaluation

- **One company and one product so far.** Only Expedia has been run, against Jira and Confluence.
- **Evaluation is thin.** One reviewer, the same person who wrote the rules; the first two counts were an AI-assisted
  read; no run is held out, and the pipeline was tuned on earlier runs on the same input.
- **The sample is partly replayed.** The sample's mapping and drafting outputs are replays of saved real runs.
- **Plan details are checked by hand, once.** The product config's plan-level details were checked by hand once, and
  the tool never states plan availability.

## Output quality

- **Semantic errors can pass the checks.** The checks verify that cited passages exist and contain the quoted evidence,
  and they flag likely problems by word overlap. They do not read meaning: one meaning-shift error (I-08, a result
  restated as an aim) passed every automated check and was found by a human reader.
- **Weak or unitless measures in some criteria.** For example O-03-C3, "transactional efficiency in payments and
  customer service", is not something a CSM can count as written.
- **Some objective questions drift from the product.** A few objective-level validation questions ask about the
  company's priorities rather than about the work the product supports.
- **Draft questions can presume the company is a customer.** On the held-out Etsy run, one criterion's questions said
  "your Atlassian Cloud apps" in all three Draft attempts; the guards flag this, but only the CSM draft check stops on
  it, and the CSM draft omits per-criterion validation questions. The detailed plan (plan.md, plan.json) can therefore
  still contain such wording, flagged, for the CSM to rewrite before use.
- **Coarse challenge grouping in the CSM draft.** The CSM draft keeps challenges by Extract's `category` and
  `change_type` fields (see [config/csm_draft_rules.yaml](../config/csm_draft_rules.yaml)), which Extract assigns
  without a check. C-08 (consumer fraud) is kept only because it shares a category with C-07; C-11 is categorised
  "other" although it is a financing risk. The grouping is a filter, not a relevance judgment; the CSM decides relevance.
- **Omitted sentences.** Three sentences are omitted from the sample: two for verbatim runs of 15 or more words, and
  one that pointed back at an omitted sentence. Shorter verbatim spans remain, cited by source link.
- **No fit is common by design.** Nine of twelve Expedia initiatives mapped to "no fit", since Jira and Confluence track
  work and do not run a travel business.

## Sources

- **Earnings releases stand in for call transcripts.** They are management's prepared statements and do not include
  analyst Q&A.
- **The 10-K can be months old.** Late in a fiscal year the latest 10-K can be up to about a year old; newer events
  come only from 8-Ks and headlines.
- **GDELT covers only the last 90 days and throttles.** Only headlines are stored (no article text), headline-only
  claims stay `inferred`, and repeated calls can hit HTTP 429.

See [how_it_works.md](how_it_works.md) for how each source is used and dated.
