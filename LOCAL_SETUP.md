# MiroFish with your ChatGPT plan

This fork adds a local ChatGPT subscription adapter and SQLite graph memory.
You do not need an OpenAI Platform API key, Zep account, Docker, or a downloaded
LLM. The interface, documents, graph database, and simulation state stay on your
computer. Prompts, document excerpts and agent context used for inference are
sent to OpenAI under the ChatGPT account you connect. Model inference is online.

## Start

Prerequisites: Node.js 22.12 or later, Python 3, and `uv`. The backend selects
Python 3.12 through `backend/.python-version` and uses its own virtual environment.

```sh
npm run setup:local
npm run dev
```

Open <http://127.0.0.1:3000>. The backend listens on `127.0.0.1:5001`.
The setup script creates `.env` only when it does not exist. It generates random
local adapter/session keys and preserves existing configuration on subsequent runs.

1. Select **Continue with ChatGPT**. Sign in and authorize **MiroFish Local** to
   use your plan. This is the documented public OAuth flow for locally hosted
   open-source apps; it does not read Codex credentials or ChatGPT conversations.
2. Return to MiroFish and choose a model from your account catalog.
3. Select **Test connection · 1 request**. Only a completed inference response
   confirms access; a model listed in the catalog alone is insufficient.
4. Start with a short document describing 3–5 explicit personas and one question.
   Simulations in subscription mode are capped at three rounds by default.

ChatGPT Plus/Pro eligibility, workspace policy and plan limits apply. No paid API
fallback is configured. If account access is unavailable, requests fail with an
actionable error rather than changing billing paths.

## Limits and controls

- The adapter admits at most **100 inference attempts per backend session** and
  **two simultaneous requests**. Waiting requests queue for up to 60 seconds.
  Failed admitted attempts count toward the local request budget.
- Account/model errors and upstream 401/403/429 responses pause requests. Review
  the account connection and **Manage plan usage** before explicitly resetting
  the local counter. Resetting this counter does not reset your ChatGPT allowance.
- Stopping/restarting the backend starts a new local counter. This counter is a
  request guard, not a token estimate or a measurement of your remaining plan.
- `.env` contains `CHATGPT_MAX_REQUESTS`, `CHATGPT_MAX_CONCURRENT` and
  `OASIS_DEFAULT_MAX_ROUNDS`. Increase these deliberately after a small trial.
- The model name `chatgpt-plan` is a local alias for the model selected in the UI.
  All MiroFish and CAMEL agent calls use the same adapter. Subscription requests
  use the public Responses endpoint, with local execution of simulation tools.
- Reasoning context for tool conversations is kept only in this backend's memory
  (up to 16 MiB, 256 output groups and 24 hours), partitioned by account and model.
  A backend restart, sign-out, model change or cache expiry can require starting
  the affected simulation again. Missing context fails explicitly; it is not
  silently discarded or regenerated.

## Local files and sign-out

`.env`, `.local/`, uploads, logs and local browser-test output are ignored by Git.
OAuth credentials use owner-only files in `.local/chatgpt/`; the browser receives
connection status and account/model information, never the access/refresh tokens.
The graph lives in `.local/graphs.sqlite3`. Keep these local files private.

**Sign out** removes this installation's account credentials while retaining the
issued client registration and stable host ID for later sign-in. **Switch account**
starts an explicit new registration. To revoke the
registration at OpenAI, disconnect MiroFish from ChatGPT settings under
**Security and login → Sign in with ChatGPT**. Do not share one signed-in backend
with other users; this setup intentionally accepts loopback access only.

The SQLite backend extracts entities and facts through your configured model. It
uses lexical retrieval and normalized-name entity merging. It preserves source
episodes and conflicting facts; it has no semantic reranker or automatic temporal
invalidation. The original Zep Cloud mode remains available with
`MEMORY_BACKEND=zep` and `ZEP_API_KEY`.

## Verification

```sh
cd backend
uv run pytest tests -q
cd ..
npm run build
```

Automated tests cover OAuth state/PKCE/identity/refresh, local access restrictions,
request limits, completed streaming responses, function-tool roundtrips through
the installed CAMEL SDK, SQLite extraction/batch/persistence/failure behavior,
and existing backend contracts. Mocked requests do not establish account access:
the real connection test requires your own browser authorization.

The live smoke check also passed with GPT-6 Luna: a nonempty connection reply,
three extracted graph nodes/two relationships, and two tool-created posts in a
one-round native OASIS simulation. That check starts with an empty feed. Seeded
or longer Twitter runs can download an additional recommendation embedding model;
first startup can also take time to load the ML libraries. Those larger runs
have not been performance-tested by this smoke check.

AI personas provide hypotheses about behavior. Check important findings with
real users and application analytics before making product decisions.

## Integration references

- [ChatGPT plan usage in open-source apps](https://developers.openai.com/siwc/token-sharing-open-source)
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
