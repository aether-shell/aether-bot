---
name: weather
description: Get current weather and forecasts (no API key required). Use when the user asks for live weather conditions, forecasts, or weather-based planning.
homepage: https://wttr.in/:help
metadata: {"nanobot":{"emoji":"🌤️","requires":{"bins":["curl"]},"aliases":["forecast"],"triggers":["weather","forecast","temperature","rain","snow","wind","humidity","today weather","天气","气温","温度","降雨","下雨","下雪","风力","今天天气","天气预报"],"allowed_tools":["exec","web_search","web_fetch"],"tool_round_limit":true,"tags":["realtime","network","weather"]}}
---

# Weather

Two free services, no API keys needed.

Execution rules:
- Use `curl -sS --connect-timeout 5 --max-time 8` for each network call.
- Do not chain multiple HTTP requests in one `exec` command (`&&`, `;`, pipes).
  Run one request per tool call, inspect output, then decide next step.
- Avoid `wttr.in?...?2` style full-text forecast endpoints in automation; they
  are unstable in some networks. Use `format=` for current weather and
  Open-Meteo JSON for forecast fields.

## wttr.in (primary)

Quick one-liner:
```bash
curl -sS --connect-timeout 5 --max-time 8 "wttr.in/London?format=3"
# Output: London: ⛅️ +8°C
```

Compact format:
```bash
curl -sS --connect-timeout 5 --max-time 8 "wttr.in/London?format=%l:+%c+%t+%h+%w"
# Output: London: ⛅️ +8°C 71% ↙5km/h
```

Full forecast:
```bash
curl -sS --connect-timeout 5 --max-time 8 "wttr.in/London?T"
```

Format codes: `%c` condition · `%t` temp · `%h` humidity · `%w` wind · `%l` location · `%m` moon

Tips:
- URL-encode spaces: `wttr.in/New+York`
- Airport codes: `wttr.in/JFK`
- Units: `?m` (metric) `?u` (USCS)
- Today only: `?1` · Current only: `?0`
- PNG: `curl -sS --connect-timeout 5 --max-time 8 "wttr.in/Berlin.png" -o /tmp/weather.png`

## Open-Meteo (fallback, JSON)

Free, no key, good for programmatic use:
```bash
curl -sS --connect-timeout 5 --max-time 8 "https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.12&current_weather=true"
```

Find coordinates for a city, then query. Returns JSON with temp, windspeed, weathercode.

Docs: https://open-meteo.com/en/docs
