# Product testing in MiroFish Local

Status: implemented locally, 24 October 2026. First impressions, independent screenshot comparisons, public read-only browser walkthroughs, evidence exports, and a human-validation-plan export are available at `/product-testing`. Actual human recruitment and live-traffic instrumentation remain work in the product being tested.

## Implementation result

- Directly editable hypothetical participants bypass the original social graph's requirement for extracted people.
- Screenshot and public-URL studies freeze verified images and isolate researcher context from participant sessions.
- Comparisons randomize independent session order and hide original variant labels and preferred hypotheses during evaluation. Identical images trigger an explicit control result.
- Public browser tasks record screenshots, visible-page observations, actions, boundary restrictions, and deterministic success checks in isolated contexts.
- SQLite retains progress, raw responses, source hashes, model/prompt versions, request counts, and interrupted attempts. Resume, cancellation, account isolation, and explicit budget changes are implemented.
- The initial capture verification froze a loading screen. It is retained with a quality warning; readiness checks now stop incomplete captures before inference. A replacement ready-screen Map of XP study completed successfully.
- A live Map-to-Shorts walkthrough reached its configured destination, and the identical-image comparison correctly refused to infer a design difference.

Browser automation currently supports public navigation, scrolling, and clicking. Text entry, authenticated flows, publishing, live-traffic assignment, and analytics instrumentation are outside this release. Use screenshots for private interfaces and the exported human-validation plan for subsequent research.

## Outcome

Add a Product Testing mode for first impressions, independent comparisons of two designs, and later browser task walkthroughs. Use Map of XP as the first study. Keep the existing document-led social simulation available as a separate workflow.

The output is an evidence-backed AI evaluation that helps identify hypotheses and decide what to investigate with people. It must never present simulated preferences, model repetitions, or agent task completion as human conversion rates or statistically established A/B lift.

## Baseline at planning time

The starting fork had a working ChatGPT subscription connection, a model selector, request and concurrency limits, local graph storage, document upload, persona generation, and reports. Its model adapter accepted text only. The implementation above adds screenshot studies and browser participants; actual live-traffic measurement remains separate.

The existing upload endpoint also generates an ontology. It can prepare a background project now, but that does not run a product test. Store the Map of XP source brief locally and upload it through that endpoint; do not run a social simulation as a substitute for usability research.

## Study design

Maintain two distinct information sets:

- **Researcher context:** product brief, source provenance, intended behavior, feature availability, and evaluation rubric.
- **Participant context:** a clearly labeled hypothetical persona, its immediate goal, and only the screen or information actually shown to it.

For first impressions, participants do not receive the product brief, expected answers, source-page explanation, other participants' feedback, or previous variants. Even a correct product description in memory would invalidate a comprehension test. The evaluator can read the brief after participant responses are saved.

Start with three hypothetical perspectives: a Karachi resident finding an outing, a visitor unfamiliar with the city, and a person considering contributing an experience. These are study hypotheses, not validated customer segments. Change them when actual interviews or usage evidence becomes available.

## Delivery sequence

### 0. Prepare Map of XP context — current task

- Summarize the founder's description and https://mubash.dev/mapofxp into a dated Markdown brief with direct source links.
- Distinguish public browsing, invitation-only posting, sample design previews, and unreleased Keepsakes.
- Preserve the crowdsourced-guide analogy without inventing venue scores or professional endorsements.
- Upload the brief to a named local MiroFish project and verify the stored file and extracted text. Any generated ontology or graph is background preparation only.

Acceptance: the saved project contains the correct source document, can be reopened, and has no claimed product-test results.

Completed: the brief is uploaded and indexed in the local project **Map of XP - product background**, at http://127.0.0.1:3000/process/proj_244e9c7a3825. The stored upload matches the prepared file, and the extracted text preserves the feature-availability boundaries. No product test or social simulation has been run for this project.

### 1. First-impression studies — first implementation

Add a study setup screen with the product, screenshot or page URL, viewport, audience perspectives, and questions. The URL points to the actual application for an app study; the marketing page is a separate possible stimulus.

Capture and freeze the stimulus before running participants. Record the screenshot, URL, capture time, content hash, language, viewport, and public-page state. Begin with an uploaded screenshot or public page; authentication and invitation-only flows come later.

Use one fresh model session per persona and stimulus. Ask neutral questions such as:

1. What do you think this product lets you do?
2. Who does it seem intended for?
3. What would you do first, and where would you click?
4. What is unclear or makes you hesitate?

Save verbatim answers before a separate evaluation pass. Report comprehension, misleading expectations, likely click targets, and specific points of friction, each tied to a visible screen region or quote. A screenshot inspection is not a human five-second test, eye-tracking study, or measurement of attention.

Acceptance: a participant cannot access the hidden product brief; a screenshot-backed baseline report can be reproduced from the saved study inputs; every finding cites evidence; missing or ambiguous information stays explicit.

### 2. Independent A/B design comparisons

Build on the first-impression runner. Require two explicitly supplied variants and a single written hypothesis. Keep persona definitions, tasks, viewport, location setting, model version, prompt version, and evaluation rubric consistent. Each persona sees each variant in a different fresh session. Randomize execution order and conceal the preferred variant from the evaluator where practical.

Report findings by scenario and persona, including disagreements and inconclusive outcomes. Preserve raw sessions and stimulus hashes so a comparison is auditable. Repetitions probe model consistency; they do not increase the number of real participants or justify significance claims. Avoid an unexplained aggregate winner score.

Acceptance: A cannot leak into B's history; swapping variant labels does not change the interpretation; the report shows both supporting and contradicting evidence and identifies any invalid or incomplete sessions.

### 3. Browser task walkthroughs

Add an isolated browser session per participant, a fixed test location, action and request limits, step screenshots, and explicit success criteria. Follow only information exposed in the page, without private source-code knowledge or hidden product context.

Begin with public, reversible journeys: find an experience in Karachi, open its details, and navigate between a Short and its linked experience. Record attempted actions, incorrect turns, blockers, and observed task completion. Agent completion and elapsed agent execution time must be labeled as such; they are not human usability rates or time on task.

Use staging and dedicated test accounts for posting and permission-dependent tasks. Test invitation eligibility separately from interface confusion. Require explicit authorization before publishing content or otherwise affecting external users. Persist incomplete runs and stop cleanly on authentication or usage failures.

Acceptance: a saved trace demonstrates whether the defined goal was reached; the report distinguishes a product problem from missing test data, access restrictions, or a runner failure.

### 4. Validate findings with people

Use the strongest hypotheses to prepare short human sessions. For real A/B measurement, define an eligible population, random assignment, a primary behavioral metric, exposure tracking, and event validation in the product. Determine a suitable sample size and duration from the actual baseline and target effect before interpreting lift. Keep this phase separate from AI session counts.

## Initial Map of XP study backlog

| Study | Hypothesis or question | Required input |
| --- | --- | --- |
| App baseline | Do people understand that pins represent individual experiences and know what to open first? | Current public application capture |
| Positioning comparison | Does plain language about discovering and sharing local experiences communicate the product better than the crowdsourced-guide analogy? | Two approved copy variants on otherwise identical screens |
| Discovery comparison | Does a map-led or stories-led entry better explain how to find an outing? | Two supplied prototypes; this is a candidate experiment, not an assumed existing design |
| Trust and usefulness | What visible details help someone judge whether an experience is relevant to their own visit? | Experience detail screen and a concrete outing scenario |
| Invitation clarity | Can someone distinguish browsing access from permission to publish? | Pilot access and publishing-entry screens |
| Public task | Can an agent find a Karachi experience and follow the link from a Short to its full account? | Stable public pilot data and verified task criteria |

Do not include Keepsake creation, exports, ratings, or saved-booklet tasks as existing features. The source page does not establish those as released flows.

## Implementation outline

- Add a **Product Testing** entry with study setup, progress, session evidence, and a comparison report. Existing social simulation screens remain available.
- Add a dedicated experiment service and routes rather than forcing browser actions through the Twitter/Reddit simulation loop. Reuse the account connection, model selection, usage controls, and file-storage conventions.
- Store studies, variants, personas, runs, observations, and findings in local SQLite, with screenshots and transcripts in the local workspace. Record schema versions, prompt versions, model IDs, source provenance, and stimulus hashes.
- Extend the subscription adapter to translate image inputs and preserve the existing tool-call continuation behavior. Verify image support using one small request with the selected account model before building a workflow that depends on it. If unsupported, stop with a clear explanation or use a user-selected compatible model; do not silently switch to a paid API.
- Add a separate browser runner in phase 3. The first milestone only needs a screenshot reader and isolated model sessions, which keeps the initial build small.
- Use durable run states: draft, queued, running, completed, failed, and cancelled. A report must identify missing runs; it cannot turn partial output into a successful experiment. Cancellation stops further requests and browser actions.

## Usage and handling of data

Keep the existing subscription route and shared plan limits. Before starting, show an upper bound on model requests and enforce a per-study cap in addition to the workspace cap. Failed admitted requests count. Do not reset limits or retry indefinitely.

The first pilot is three perspectives × two variants = six independent participant sessions. With one response per session and a bounded evaluation/report pass, target at most twelve model requests. This is an engineering budget, not a claim about plan entitlement. Browser studies need a separate budget for action turns and screenshots.

Storage and browser execution stay local; model inference sends the selected context and screenshots to the connected provider. Show that fact in setup, redact credentials from logs, and start with public screens and synthetic test data. Restrict automated captures to the configured product origin, validate redirect destinations, and block arbitrary local/network targets. Other sites and documents are untrusted content, not instructions to the runner.

## Verification and release gates

1. Verify image input and structured output through the real subscription connection with a small bounded check.
2. Test isolation between product context, participant sessions, variants, and accounts, plus interrupted responses, image validation, budget enforcement, cancellation, and resumable persistence.
3. Run the first-impression baseline against one frozen Map of XP capture and manually audit all findings against it.
4. Run an identical-image control: a material reported design difference is a failure requiring investigation. Check variant-label and execution-order sensitivity.
5. Release comparisons after those controls pass. Release browser tasks after a known successful journey and a deliberately blocked journey both produce correct evidence and status.

The first useful release is phases 1 and 2. Phase 3 follows once screenshot studies are reliable. Phase 4 establishes which AI findings survive contact with actual users.

## Sources and local references

- Map of XP product page: https://mubash.dev/mapofxp
- Application linked by that page: https://mapofxp.com/
- Background on interpreting synthetic-user feedback: https://www.nngroup.com/articles/synthetic-users/
- Local source brief: `.local/research/mapofxp/product-description.md`
- Existing upload and project flow: `backend/app/api/graph.py`, `backend/app/models/project.py`, and `frontend/src/views/Process.vue`
- Existing text-only model adapter: `backend/app/subscription_proxy.py`
