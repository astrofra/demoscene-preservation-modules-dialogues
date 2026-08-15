# HARFANG Semantic Navigation Interface

## 1. Purpose

This document defines a roadmap and functional specification for a gamepad-driven 3D navigation interface, implemented in Lua with HARFANG, for exploring the MODialogues corpus module by module through semantic proximity.

The interface is not a generic graph viewer. It is a spatial reading device for moving from one module to the next through relationships extracted from tracker files: authors, aliases, greetings, collaborations, events, groups, contact traces, instrument naming habits, audio-semantic affinities, and other textual or sonic clues embedded in the modules.

## 2. Source Context

The specification is based on the current repository state:

- `documentation/` defines the pipeline as a readable JSON-based workflow and already frames graph quality as imperfect.
- `data/parsed_metadata/` contains per-module extracted metadata such as `author_guess`, `sample_names`, `song_message`, `greets_rule_based`, and rule-based labels.
- `data/summaries/` contains selective LLM enrichments with mentions, aliases, person names, groups, events, places, and relationship notes.
- `data/graphs/handles_graph.json` contains a large directed graph, but it still includes noisy nodes such as dates, fragments, contact strings, and weakly normalized mentions.
- future prepared datasets may also include offline audio descriptors and coarse instrument-family guesses derived from extracted samples.

This matters because the 3D interface must expose discovery while also protecting the user from raw graph noise.

## 3. Product Vision

The user navigates a semantic landscape where each stop is centered on one module. Nearby visible choices are not arbitrary neighbors in a force-directed graph; they are a curated set of semantically meaningful next steps.

The core interaction model is:

1. arrive at one module
2. inspect its textual traces and contextual metadata
3. reveal the strongest outgoing semantic paths
4. choose one path with the gamepad
5. travel to the next module or cluster
6. continue the exploration as a chain of situated discoveries

The interface should feel closer to moving through memory chambers or linked archival constellations than to piloting a free camera over a database.

## 4. Experience Principles

- Gamepad-first: all primary actions must be reachable without keyboard or mouse.
- Near-to-near navigation: movement should prefer adjacent semantic steps over global teleportation.
- Readable over exhaustive: show a small number of high-confidence links first.
- Spatial meaning: distance, scale, color, and motion must communicate relation type and confidence.
- Archival honesty: ambiguity, missing data, and weak inferences must remain visible.
- Runtime clarity: the first HARFANG milestone must be a simple runnable vertical slice with explicit debug surfaces.

## 5. Scope

### In Scope

- a real-time 3D exploration interface in HARFANG and Lua
- module-centric navigation through semantic neighbors
- gamepad locomotion and selection
- visual representation of relation types and confidence
- layered access to parsed metadata and summary content
- lightweight filters for relation families
- debug overlays for inspecting graph and scoring behavior

### Out of Scope for the First Version

- full corpus free-flight visualization
- web deployment
- in-engine editing tools
- automatic graph correction during runtime
- advanced audio playback synchronization
- VR/XR support

## 6. Data Reality and Design Consequences

The current corpus has uneven quality. The interface must account for that explicitly.

Observed issues:

- `author_guess` is sometimes wrong or overly literal, for example directory names such as `1999`.
- some XM files currently expose almost no textual payload.
- some summaries are rich and high-confidence, while others are skipped.
- the graph export still promotes many raw text fragments into nodes.

Design consequences:

- the runtime must not rely on `handles_graph.json` alone.
- the interface needs a curated semantic layer built from `parsed_metadata` plus `summaries`.
- every link shown to the user needs a `confidence`, `relation_type`, and `evidence` payload.
- the navigation model must allow hiding or demoting noisy relations.

## 7. Conceptual Data Model for the Interface

The runtime should introduce an exploration-specific graph, generated offline before launch.

### Primary Node Types

- `module`
- `person_handle`
- `group`
- `event`
- `place`
- `instrument_token`
- `audio_profile_cluster`
- `theme_cluster`

### Primary Edge Types

- `authored_by`
- `alias_of`
- `member_of`
- `greet_to`
- `mentions`
- `collaborates_with`
- `presented_at`
- `located_in`
- `shares_instrument_vocabulary`
- `shares_tessitura_profile`
- `shares_timbral_profile`
- `harmonic_percussive_affinity`
- `contains_voice_like_material`
- `shares_contact_pattern`
- `same_source_file_across_mirrors`
- `possibly_related`

### Runtime Rule

The user always occupies a `module` as the current anchor, even when clusters or people are visible around it. This preserves the project goal of moving from module to module rather than drifting into an abstract social graph.

## 8. Spatial Metaphor

The recommended metaphor is a semantic orbit system.

- The current module sits at the center of the scene.
- First-ring neighbors are the strongest directly connected modules.
- Secondary entities appear as orbital markers around the current module.
- Relation families are grouped into sectors or lanes around the player.
- The next selected module moves into the center while the previous one recedes into the background trail.

This avoids the chaos of rendering the full graph while preserving spatial continuity.

## 9. Navigation Model

### Core Movement

- Left stick: rotate around the current module and adjust local viewing angle.
- Right stick: inspect details, tilt camera, and browse visible link candidates.
- South button: travel to selected neighbor.
- East button: step back in navigation history.
- West button: toggle semantic layer view.
- North button: pin current module or bookmark it.
- Shoulder buttons: cycle relation families.
- Triggers: expand or contract semantic radius.
- D-pad: move between candidate links in a discrete, legible order.
- Start: open pause/system menu.
- Select/View: open debug and evidence overlay.

### Movement Philosophy

The player should not need analog free-flight to make progress. The default movement is assisted traversal between curated semantic anchors.

Optional free-look may exist, but it must remain secondary.

## 10. Visual Language

### Module Representation

- Each module is a tangible object, not just a label.
- The object should react to text density, metadata richness, and confidence.
- Sparse modules should feel quieter and smaller.
- socially rich modules should feel more structured and luminous.
- modules with strong sonic identities may also expose visible cues for brightness, register, or harmonic versus percussive balance.

### Relation Representation

- greetings: warm directional arcs
- aliases: mirrored or braided links
- collaborations: paired beams or dual anchors
- events and groups: larger gate-like landmarks
- instrument vocabulary links: thinner rhythmic filaments
- timbral affinity links: soft spectral ribbons
- shared tessitura links: layered vertical bands or altitude-coded rails
- voice-like or choir-like affinity: breath-like halos or grouped resonance arcs
- low-confidence links: faded, unstable, or noisy particles

### Temporal Trace

The traversal history should remain visible as a soft trail, allowing the user to perceive their path through the corpus.

## 11. Information Layers

### Layer 1: Immediate Read

- module title
- author guess
- format
- year if available
- dominant audio profile when available
- top relation family

### Layer 2: Evidence

- extracted text fragments
- greeting targets
- LLM summary
- mentions
- relationship notes
- audio role labels
- coarse family guesses
- register or tessitura profile
- source provenance

### Layer 3: Critical Context

- confidence score
- why this link is shown
- whether the link comes from rules, summary inference, or both
- ambiguity flags
- missing-data indicators

The interface must always let the user understand why a next step exists.

## 12. Neighbor Selection Strategy

The runtime should display only a shortlist of candidates at a time.

Recommended scoring dimensions:

- directness of relation
- relation confidence
- archival value
- audio-semantic affinity
- diversity of relation family
- recency within the user session
- anti-loop penalty

The default shortlist should contain 5 to 9 candidate modules.

## 13. Offline Preparation Layer

Before the HARFANG runtime starts, an offline script should generate a clean navigation dataset dedicated to the 3D interface.

This preparation step should:

- merge `parsed_metadata` and `summaries`
- merge future `audio_features` descriptors when available
- normalize author, alias, and greeting targets
- discard clearly invalid graph nodes
- score relation confidence
- build module-to-module adjacency lists
- derive thematic clusters from repeated handles, groups, events, instrument tokens, and audio profiles
- derive module-level audio profiles from per-sample descriptors and later from playback-aware tessitura
- export compact runtime JSON files for Lua

This layer is mandatory. Runtime graph cleanup alone would be too fragile and too expensive to reason about.

## 14. Runtime Architecture in Lua

Following HARFANG best practices, the application should be structured as small explicit modules.

Recommended Lua modules:

- `main.lua` for startup and frame loop
- `app_state.lua` for global runtime state
- `data_store.lua` for loading prepared JSON
- `navigation_graph.lua` for neighbor scoring and traversal
- `scene_builder.lua` for creating visual anchors and relation geometry
- `camera_controller.lua` for gamepad-driven camera behavior
- `gamepad_input.lua` for device polling and action mapping
- `module_view.lua` for current module presentation
- `hud.lua` for textual overlays
- `debug_overlay.lua` for confidence, scoring, and data inspection
- `transition_system.lua` for travel animation between modules

## 15. HARFANG Production Constraints

The implementation roadmap should respect the following HARFANG constraints:

- establish a visible runnable baseline first
- keep source assets, generated runtime data, and compiled assets separate
- load runtime assets from compiled locations only
- keep gamepad interaction observable through debug overlays
- validate missing nodes, bad data, and empty neighbor lists early
- keep expensive semantic preprocessing outside the real-time loop

## 16. Vertical Slice Definition

The first meaningful prototype should not target the full corpus.

It should include:

- 30 to 100 carefully prepared modules
- at least three relation families: `greet_to`, `alias_of`, `collaborates_with`
- one optional audio-semantic relation family once offline descriptors are available
- one working gamepad navigation loop
- one readable 3D module presentation
- one evidence overlay
- one debug screen showing why each candidate neighbor is ranked

If this slice is not satisfying, scaling to the full dataset should be postponed.

## 17. Roadmap

### Phase 0: Corpus Preparation Spec

- define the runtime navigation JSON schema
- choose confidence rules for each relation family
- define module-to-module projection rules from handle-level relations
- define confidence rules for audio-semantic edges separately from text-derived edges
- identify blacklists and normalization rules for noisy graph nodes

### Phase 1: Offline Semantic Navigation Builder

- build a new preparation script dedicated to the interface
- merge parsed metadata and summaries into exploration records
- reserve extension points for offline audio descriptors and aggregated module audio profiles
- generate curated adjacency lists and evidence bundles
- export a small fixture dataset for rapid iteration

### Phase 1B: Audio-Semantic Dataset

- extract or ingest per-sample audio descriptors prepared offline
- aggregate coarse timbral, role, and register profiles at module level
- add audio-derived candidate edges with lower default confidence than direct textual evidence

### Phase 2: HARFANG Runtime Shell

- create the Lua project skeleton
- initialize window, renderer, input, and frame loop
- load prepared runtime data
- render one visible module anchor and one camera path

### Phase 3: Gamepad Navigation Core

- implement device polling and action mapping
- build candidate selection and assisted traversal
- implement travel transitions between modules
- add history stack and backtracking

### Phase 4: Spatial Semantics

- render first-ring module neighbors
- visualize relation families with distinct geometry and color codes
- add confidence-based visual modulation
- add distinct cues for timbral, tessitura, and harmonic or percussive relations
- add traversal trail and orientation cues

### Phase 5: Evidence and Critical Reading

- add HUD panels for text fragments and summaries
- add module-level audio profile panels when available
- show provenance, confidence, and ambiguity flags
- support relation-family filtering and evidence drill-down

### Phase 6: Debugging and Tuning

- add a developer overlay for ranking inputs and rejected neighbors
- expose dataset health metrics
- test failure cases: empty module, noisy author, skipped summary, alias conflict

### Phase 7: Scale-Up

- expand from the fixture corpus to larger prepared subsets
- measure readability and performance
- tune neighbor selection to avoid repetitive or trivial paths

## 18. Acceptance Criteria

The specification is satisfied for the first production-ready prototype when:

- a user can start on one module and reach another only through explicit semantic choices
- the interface remains understandable with a gamepad alone
- every shown transition has inspectable evidence
- noisy or low-confidence relations are visibly demoted
- the runtime remains stable on a curated subset
- the visual experience communicates semantic structure rather than generic graph density

## 19. Open Risks

- current graph noise may still dominate unless offline normalization is strict
- author attribution quality may distort traversal paths
- sparse XM/S3M/IT parsing may bias exploration toward MOD-rich records
- audio-family guesses may look more certain than they actually are unless confidence is explicit
- raw sample pitch may be mistaken for true played tessitura unless pattern-aware analysis is added later
- too much visual complexity may hide the archival evidence instead of clarifying it
- a full-corpus render strategy may collapse readability if introduced too early

## 20. Recommended Next Deliverable

The next concrete deliverable after this specification should be a data-preparation design document that defines the exact runtime JSON schema and the scoring rules used to convert corpus artifacts into module-to-module navigation candidates.
