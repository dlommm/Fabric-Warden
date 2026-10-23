# AI provider setup

Provided by the `ai-providers` module.

Open **AI providers** (needs `ai:manage` to add or change; `ai:use` to view). Every provider is tested before it is saved: Fabric Warden lists its models and keeps the list.

## Kinds

| Kind | Default URL | Key | Endpoints used |
|---|---|---|---|
| Ollama | `http://localhost:11434` | optional (for a proxy) | `GET /api/tags`, `POST /api/chat` (stream off, tools in function format) |
| OpenAI-compatible (LM Studio, vLLM, llama.cpp server, OpenAI) | `http://localhost:1234/v1` | optional | `GET /v1/models`, `POST /v1/chat/completions` with `tools` |
| Anthropic Claude | `https://api.anthropic.com` | required | `GET /v1/models`, `POST /v1/messages` (`anthropic-version: 2023-06-01`) |
| Offline demo model | built in | none | in-process; deterministic, not a language model, labelled demo |

The model must support tool (function) calling; agents depend on it. With Ollama, use a model tagged for tools. In Docker Compose, `localhost` is the Fabric Warden container itself: use the host's LAN address or a service name.

Current Claude model ids: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`. The default model is the first one the provider lists unless you choose one.

## Keys

API keys go into the credential vault (Fernet, master key outside the database). Responses only carry a fingerprint; a key can be replaced, never read back. Deleting a provider deletes its key.

## What leaves the network

A local provider keeps everything on your network. With an API provider, each agent run sends the agent's instructions, your question and the results of the tools it calls (device and client names, addresses, metrics, log lines, firewall policies) to that provider. The provider list states where prompts go for each provider.

## Budgets and cost

- Per provider, per calendar month (UTC): an optional token limit and an optional cost limit. When either is reached, runs on that provider stop before their next model call and new runs end at once as "budget reached".
- Per agent: tokens, tool calls and model turns per run (Agent Center › Settings).
- Cost is an estimate from the tokens the provider reports. Claude models use Anthropic's published first-party prices per million tokens: Opus 5.5 $4 / $20, Sonnet 5.5 $2 / $10, Haiku 5.5 $0.10 / $0.50 (input / output; Haiku is priced higher above 100K prompt tokens, which agent runs do not reach). Other providers use the price set on the provider (0 by default for local servers).

## Refusals

If Claude declines a request (`stop_reason: refusal`) the run fails with "declined". For Opus 5.5 and Sonnet 5.5 on the official endpoint, requests opt into Anthropic's server-side refusal fallback (`fallbacks: "default"`); turn it off with `PATCH /api/ai/providers/{id}` `{"refusal_fallback": false}`.

## Troubleshooting

| Result | Meaning |
|---|---|
| auth | The key was rejected. |
| unreachable | Nothing answered at that URL and port from the Fabric Warden container. |
| tls | The certificate could not be verified. |
| not_found | The server answered but lacks the endpoint (check the `/v1` part for OpenAI-compatible servers). |
| rate_limited | The provider is throttling; try later. |

