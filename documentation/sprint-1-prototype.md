# MODialogues Prototype Sprint 1

## Goal

This first iteration delivers an executable prototype of the pipeline, with a deliberately narrow perimeter.

The main goal is to validate the workflow shape:

1. discover or seed module files
2. store pipeline progress in readable JSON state files
3. parse module text with a reliable first parser
4. classify obvious cases locally without an LLM
5. call Ollama only when the local layer is not enough
6. export a first social graph

## Implemented Perimeter

### Included in Sprint 1

- JSON state store under `data/state/`
- atomic state file writes
- resumable download state
- deduplication by SHA-256
- source discovery for:
  - `http_index`
  - `local_dir`
- reliable parsing for `.mod`
- minimal title-level parsing for `.xm`, `.s3m`, `.it`
- local detection of common instrument labels
- first-pass rule-based classification:
  - `instrument_only`
  - `greeting`
  - `signature`
  - `work_offer`
  - `contact`
  - `technical_note`
  - `credits`
  - `unknown_social`
- selective Ollama summarization
- summary reuse based on `input_text_hash`
- graph export in:
  - JSON
  - DOT
  - GEXF
- thin orchestration script

### Explicitly Out of Scope in Sprint 1

- complete XM/S3M/IT structural parsing
- embeddings in the critical path
- web UI
- concurrency
- remote timestamp parsing from arbitrary HTML listings
- advanced retry policies
- full scholarly annotation model
- full corpus crawling optimizations

## Technical Choices for This Iteration

### Why JSON State Instead of SQL

The prototype uses `remote_files.json`, `modules.json`, and `summaries.json` so the current state can be read directly without a database tool.

This makes manual inspection easier during early iteration.

### Why `local_dir` Sources Are Included

The remote crawl is the real target, but it is slow to test.

The `local_dir` source type exists so the pipeline can be exercised locally on a small fixture corpus during development.

### Why MOD Is the Primary Supported Format

MOD parsing is the most mature part of the prototype because:

- it is central to the initial research angle
- its structure is simpler to parse reliably
- it is enough to validate the social-text pipeline before expanding to richer formats

## Known Limits

- XM, S3M, and IT support is currently best-effort and incomplete
- sample or instrument extraction is not yet implemented for XM, S3M, and IT
- the HTTP crawler assumes plain directory listings with standard links
- the LLM prompt contract is strict, but a local model can still return invalid JSON
- graph quality depends heavily on author guessing and rule-based greet extraction

## Definition of Done for Sprint 1

Sprint 1 is considered successful when a developer can:

1. point the pipeline at a small local directory of module files
2. run discovery and download
3. parse the resulting files
4. observe that obvious instrument-only or greeting-only fragments do not trigger the LLM
5. generate graph exports from the parsed and summarized outputs

## Suggested Validation Workflow

For local validation, prefer a tiny `local_dir` source with a few handcrafted or known-good MOD files.

Recommended order:

1. run `fetch_modules.py`
2. inspect `data/state/remote_files.json`
3. run `parse_modules.py`
4. inspect `data/parsed_metadata/`
5. run `run_ollama.py`
6. inspect `data/state/summaries.json`
7. run `build_graph.py`
8. inspect `data/graphs/`

## Next Sprint Candidates

- extend real parsing for XM, S3M, and IT
- improve author guessing
- add better greeting target extraction
- add fixture-based automated tests
- add optional embeddings for unresolved text clustering
- add corpus-level reports and statistics
