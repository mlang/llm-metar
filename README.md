# llm-sky

LLM tools for fetching environmental information.

## Installation

```bash
llm install git+https://github.com/mlang/llm-sky
```

## Usage

```bash
llm -f metar:LOWG 'How is the weather?'
llm -T 'Local("Graz")' 'Report local conditions?'
llm -T 'Local(latitude=47, longitude=15.5)' 'Report local conditions?'
llm -T geocode -T weather 'How is the weather in Quito, Ecuador?'
llm -T geocode -T sun 'When does the sun rise in Oslo?'
```
