# Audio-Semantic Enrichment Study

## 1. Purpose

This document studies whether the MODialogues pipeline can add an audio-semantic layer based on sample and instrument analysis, with `librosa` as a primary feature-extraction tool.

The goal is not to replace text-based interpretation. The goal is to complement it with another semantic axis derived from the sound material itself: register, timbre, attack profile, harmonic or percussive balance, and coarse instrument-family resemblance.

## 2. Short Answer

Yes, this is worth pursuing, but only as a probabilistic enrichment layer.

`librosa` can support:

- pitch and register estimation
- spectral and timbral descriptors
- onset and rhythmic descriptors
- harmonic versus percussive separation
- tonal descriptors for pitched material

`librosa` does not directly provide:

- a built-in classifier for `strings`, `winds`, `percussion`, `voice`, and similar archival categories
- a guaranteed notion of true tessitura for tracker instruments as actually played inside a module

Those higher-level labels would need to be inferred from descriptors, heuristics, clustering, or a separate classifier.

## 3. Why This Matters for MODialogues

The current project already extracts semantic traces from text embedded in modules. Audio-semantic enrichment would add a second reading layer:

- modules that share similar vocal or choir-like material
- modules built around bright metallic percussion versus soft sustained pads
- modules that favor low-register bass material or high-register lead material
- modules whose sample palette suggests similar scene aesthetics even when the text layer is sparse

This is especially useful for modules with weak or absent textual traces.

## 4. What `librosa` Can Realistically Provide

Based on the official `librosa` documentation:

- feature extraction includes MFCCs, mel spectrograms, chroma, tonnetz, RMS, zero-crossing rate, spectral centroid, bandwidth, contrast, flatness, and rolloff
- pitch and tuning tools include `yin`, `pyin`, and `piptrack`
- onset and tempo tools include `onset_strength` and `beat_track`
- harmonic/percussive decomposition is available through `effects.hpss` and related functions

Useful official references:

- `https://librosa.org/doc/0.11.0/feature.html`
- `https://librosa.org/doc/latest/generated/librosa.pyin.html`
- `https://librosa.org/doc/latest/generated/librosa.yin.html`
- `https://librosa.org/doc/latest/generated/librosa.beat.beat_track.html`
- `https://librosa.org/doc/latest/generated/librosa.onset.onset_strength.html`
- `https://librosa.org/doc/0.11.0/generated/librosa.effects.hpss.html`

## 5. What Needs Careful Interpretation

### 5.1 Tracker Reality

Tracker modules do not behave like ordinary audio files:

- one module contains many samples or instruments
- the same sample can be replayed at multiple pitches
- envelopes, loops, retriggering, and pattern context can radically change the perceived instrument role
- the final musical register depends on note events, not only on the raw sample waveform

Because of this, audio semantics should be split into two different problems:

1. intrinsic sample description
2. playback-aware musical usage

### 5.2 Tessitura Is Not a Single Number

If `pyin` or `yin` is run on an extracted sample waveform, it can estimate a likely fundamental or pitch region only for pitched and sufficiently stable material.

That is useful, but it is not the true tessitura of the module.

A better roadmap is:

- first estimate the native pitch center of the raw sample
- later, once pattern parsing is mature enough, derive the distribution of played notes per instrument
- aggregate both into a module-level register profile

### 5.3 Instrument Families Are Fuzzy

`strings`, `winds`, `brass`, `voice`, and `percussion` are human categories, not direct outputs of low-level DSP descriptors.

For MOD/XM/S3M/IT corpora, a more honest approach is:

- compute low-level descriptors first
- classify obvious cases conservatively
- cluster uncertain material
- expose confidence and allow abstention

## 6. Recommended Analysis Levels

### Level A: Text-Only Semantics

This is the current baseline:

- sample names
- instrument names
- song messages
- greetings
- signatures
- contacts
- LLM summaries

### Level B: Raw Sample Audio Semantics

Extract the PCM payload of each sample or instrument and compute descriptors on the raw waveform.

This level is good for:

- percussive versus sustained material
- brightness and noisiness
- likely vocal versus non-vocal character
- rough pitch center on stable samples

### Level C: Playback-Aware Tessitura

Parse pattern data and note events to reconstruct how instruments are used musically.

This level is good for:

- actual played register
- role inference such as bass, lead, pad, stab, drone
- repeated pitch habits across modules

### Level D: Module-Level Audio Profile

Aggregate sample- and event-level features into one module profile.

This level is good for:

- module-to-module similarity
- graph edges for sonic affinity
- 3D navigation filters such as `voice-like`, `percussive`, `low-register`, `bright`, or `harmonically dense`

## 7. Proposed Descriptor Families

### 7.1 Spectral and Timbral

- MFCC summary statistics
- mel spectrogram statistics
- spectral centroid
- spectral bandwidth
- spectral contrast
- spectral flatness
- spectral rolloff
- zero-crossing rate
- RMS energy

These support broad notions such as brightness, noisiness, density, and attack softness.

### 7.2 Pitch and Register

- `pyin` median F0 on voiced regions
- `pyin` voiced ratio
- `yin` fallback for difficult monophonic material
- pitch histogram
- low, mid, and high register occupancy

These support rough tessitura and role hints such as bass-like or lead-like.

### 7.3 Rhythmic and Transient

- onset strength statistics
- transient density
- attack sharpness proxy
- beat salience where meaningful

These support drum-like or pluck-like versus sustained behavior.

### 7.4 Harmonic and Percussive Balance

- HPSS harmonic energy ratio
- HPSS percussive energy ratio
- residual energy ratio where relevant

These support coarse families such as sustained harmonic material versus transient percussion.

### 7.5 Tonal Descriptors

- chroma
- tonnetz

These are useful only for pitched material and should be skipped for strongly noisy or purely percussive samples.

## 8. Proposed Label Taxonomy

The taxonomy should be layered.

### Layer 1: Objective Audio Role

- `percussive`
- `pitched_sustained`
- `pitched_plucked`
- `noise_like`
- `voice_like`
- `hybrid_or_unclear`

### Layer 2: Musical Function Guess

- `bass_like`
- `lead_like`
- `pad_like`
- `stab_like`
- `drum_like`
- `fx_like`

### Layer 3: Interpretive Family Guess

- `strings_like`
- `winds_like`
- `brass_like`
- `choir_or_voice_like`
- `guitar_like`
- `synthetic_unspecified`

Layer 3 must remain optional and confidence-scored. Many tracker samples will never justify a stable family label.

## 9. Recommended Classification Strategy

Do not start with a hard classifier for orchestral families.

Recommended order:

1. extract low-level descriptors with `librosa`
2. derive conservative rules for obvious `percussive`, `noise_like`, `voice_like`, and `pitched_sustained` cases
3. cluster the unresolved material
4. inspect clusters manually
5. only then introduce higher-level family labels

This order fits the project better than prematurely forcing every sample into a conventional instrument class.

## 10. Data and Artifact Roadmap

Recommended future artifacts:

- `data/extracted_samples/`
- `data/audio_features/`
- `data/state/audio_features.json`
- `data/reports/audio_semantic_clusters_*.json`

Recommended per-sample output fields:

- `sample_id`
- `module_id`
- `sample_index`
- `sample_name`
- `duration_seconds`
- `voiced_ratio`
- `f0_median_hz`
- `pitch_class_profile`
- `spectral_centroid_mean`
- `spectral_bandwidth_mean`
- `spectral_flatness_mean`
- `spectral_contrast_mean`
- `harmonic_ratio`
- `percussive_ratio`
- `timbral_cluster_id`
- `role_labels`
- `family_labels`
- `confidence`

Recommended per-module aggregation fields:

- `dominant_audio_roles`
- `dominant_family_guesses`
- `register_profile`
- `brightness_profile`
- `harmonic_percussive_profile`
- `voice_like_presence`
- `audio_similarity_neighbors`

## 11. Impact on the Analysis Tool Roadmap

The Python tool should eventually grow by phases.

### Phase A: Sample Extraction

- extract raw sample waveforms from supported formats
- keep sample provenance linked to module and instrument index
- deduplicate samples by content hash where useful

### Phase B: Descriptor Extraction with `librosa`

- compute low-level descriptors offline
- skip impossible cases gracefully
- record per-sample failures without stopping the batch

### Phase C: Conservative Audio Semantics

- assign coarse role labels
- expose confidence and abstention
- aggregate sample descriptors at module level

### Phase D: Pattern-Aware Tessitura

- parse note events and instrument usage
- estimate actual played register instead of only raw sample pitch
- refine module-level role inference

### Phase E: Graph and Navigation Integration

- add module-to-module sonic affinity edges
- add filters for timbre, register, and percussive or harmonic behavior
- expose the evidence chain in the UI

## 12. Impact on the 3D Interface

Audio semantics should not appear only as decoration. They should become a true navigation dimension.

Possible new relation families:

- `shares_timbral_profile`
- `shares_register_profile`
- `shares_percussive_palette`
- `voice_like_affinity`
- `bright_vs_dark_similarity`

Possible interface affordances:

- filter modules by dominant sonic family
- move from one module to another through shared timbre rather than shared text
- color-code neighbors by harmonic, percussive, vocal, or noisy character
- reveal whether a relation comes from text, audio, or both

## 13. Risks

- raw sample pitch is not the same as played tessitura
- extremely short or noisy samples may defeat pitch estimators
- synthetic tracker material often sits between acoustic families
- some formats expose richer sample structure than others
- family labels may look authoritative even when they are weak guesses

## 14. Recommendation

This direction is worth adding to the documentation and to the long-term roadmap.

The right framing is:

- `librosa` as an offline descriptor engine
- coarse audio semantics before ambitious instrument taxonomy
- confidence-scored inference rather than categorical truth
- pattern-aware tessitura as a second step, not as an immediate promise
