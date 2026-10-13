# ai-discord-bot
A Discord bot for shared AI conversations, file questions, and image generation with OpenAI.

# installation
```sh
git clone git@github.com:osuAkatsuki/ai-discord-bot.git
cd ai-discord-bot/
cp .env.example .env
nano .env # edit your settings
make build

# run the program
make run
```

there are many additional make commands, they can be found in the Makefile.

## Models and commands

The bot uses OpenAI's Responses API for conversations and the Images API for
image generation and editing. The Python SDK requirement is `openai>=3.29.0,<4`.
Model access depends on the configured OpenAI project.

New conversations offer these models (USD per million tokens, Standard processing):

| Model | Ordinary input | Cached input | Cache writes | Output |
| --- | ---: | ---: | ---: | ---: |
| `gpt-6.1-sol` (default) | $2.00 | $0.10 | $2.50 | $10.00 |
| `gpt-6-astra` | $10.00 | $1.00 | $12.50 | $50.00 |
| `gpt-6-luna` | $0.10 | $0.01 | $0.125 | $0.50 |

Sol is the general-purpose option, Astra the most capable option, and Luna the
least expensive. These models support image input and roughly 1M-token contexts.
For requests above 272,000 input tokens, input/cache rates double and output rates
increase by 50% for the entire request. Reasoning tokens are included in output
usage. Rates are maintained in `app/openai_pricing.py`.

- `/ai`: create a shared conversation thread. Mention the bot in the thread to
  request a reply. `model`, `reasoning`, and `web_search` configure the thread.
- `/model`: select the model and reasoning/search options for an existing thread.
  Omitted options reset to low reasoning and search off.
- `/query`: ask a standalone question, optionally with an attachment, reasoning
  effort, and web search. A query inside a thread counts toward that thread's cost.
- `/context`: retain 0–100 previous messages. Zero sends only the current message.
  This is a message limit, not a token budget or automatic compaction.
- `/summarize`: summarize channel history using the default model.
- `/transcript`: export the stored conversation text.
- `/image`: generate an image, or edit the image supplied as `reference`.
- `/threadcost` and `/monthlycost`: show recorded estimated costs by requester;
  monthly means the trailing 30 days.

Commands have a `dev` prefix outside production. Existing authorization checks
apply to all commands.

### Reasoning and web search

Reasoning defaults to `low`; `medium`, `high`, `xhigh`, and `max` are available.
Only Luna also supports `none`. Higher effort can increase latency and cost.

Search is off by default. Set `web_search: true` to let the model search when
useful. Search-backed answers include clickable citations. Web search costs
$0.01 per tool call plus model tokens; each provider response is limited to five
built-in tool calls. Local function tools retain a five-round continuation limit.

### Image and file inputs

Attach images, PDFs, text/code files, Word documents, presentations, or
spreadsheets to a bot mention in a tracked thread, or use `/query attachment`.
Images support PNG, JPEG, GIF and WebP. Supported document suffixes are listed in
`app/attachments.py`. The bot limits each attachment to 20 MiB and the combined
attachments in one message to less than 50 MiB. Provider limits also apply to
files replayed from conversation history.

Attachment references are retained within the thread's message context. Discord
attachment URLs can expire: reattach the file if an old reference is unavailable.
Text transcripts do not embed attachment contents. PDF inputs include page images;
non-PDF documents generally contribute extracted text. Spreadsheet input only
processes up to the first 1,000 rows per sheet, so use a separate analysis workflow
for complete large datasets.

### Image generation and editing

`/image` uses `gpt-image-2.5-flare` for generation and defaults to
`gpt-image-2.5-sunburst` when a reference is attached. Either model can be selected
explicitly. Editing accepts PNG, JPEG or WebP references up to 20 MiB. To edit a
previous result, attach it as the reference in another `/image` request.

Quality ranges from `low` through `max` (default `medium`); sizes are square,
landscape, or portrait. Set `background: transparent` for a cutout. Results are
returned as WebP files. The channel's Discord upload limit still applies; if a
result exceeds it, the bot reports that the generation was billed but could not
be uploaded.

Both image models cost $5/M text-input tokens, $8/M image-input tokens and $30/M
output tokens. Direct Images API requests do not receive cached-input discounts.
Cost varies with the prompt, reference, size and quality; it is not a fixed price
per image.

## Usage accounting

`ai_requests` stores the model, usage and estimated USD cost of each completed
provider call, attributed to the initiating user. Intermediate tool rounds,
reasoning/output tokens, cache reads/writes, web search and image requests are
included. A thread's later model changes do not reprice these records.

Reports exclude earlier messages without request-level usage records. Requests
with unavailable usage are counted as unpriced, not free. Estimates use published
Standard/global rates and are not provider invoices; they exclude account credits,
custom pricing and calls that fail without returning usage.

The normal container bootstrap applies database migrations before starting the
bot. Back up the database before running migrations; rolling back the usage-table
migration removes its recorded costs.

## Provider boundary and development

`app/ai.py` owns normalized messages, tool calls/results, responses and the provider
entry points. OpenAI wire formats, continuation state and API calls live under
`app/adapters/openai/`; pricing is computed there before usage is recorded.
Conversation use cases execute normalized tools and never construct provider API
payloads. Add a future provider adapter and route its model entries through the
shared entry points. Provider-specific continuation data is opaque to callers.

Historical model identifiers remain readable in stored threads. Older OpenAI
threads keep their chosen model; use `/model` to switch. DeepSeek identifiers are
read-only historical values: those threads must select an OpenAI model to resume.
No DeepSeek key is required.

Install development dependencies and run the offline suite with
`python -m pytest`. Tests use placeholder credentials, mock provider transports,
and do not connect to Discord or the configured database. Run `make lint` for the
repository's pre-commit checks.

Voice, streaming replies, hosted code execution, retrieval indexes, and automatic
context compaction are not implemented.

## Provider documentation

Models, capabilities and rates were checked against official documentation on
2026-10-13:

- [OpenAI models](https://developers.openai.com/api/docs/models)
- [GPT-6 API compatibility](https://developers.openai.com/api/docs/guides/latest-model)
- [Pricing](https://developers.openai.com/api/docs/pricing)
- [Prompt caching and usage](https://developers.openai.com/api/docs/guides/prompt-caching)
- [File inputs](https://developers.openai.com/api/docs/guides/file-inputs)
- [Web search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [Image generation and editing](https://developers.openai.com/api/docs/guides/image-generation)
