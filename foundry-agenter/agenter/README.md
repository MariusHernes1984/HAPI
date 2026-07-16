# ⚠️ Legacy — ikke i bruk

Disse JSON-filene beskriver det opprinnelige 3-agent-designet og leses **ikke** av noe kode.
De oppgir `gpt-4o` som deployment, noe som ikke stemmer med dagens stack.

**Faktisk kilde til sannhet:**
- Agent-definisjoner og modell: `../deploy/deploy_agents.py` (`MODEL_DEPLOYMENT`, default `gpt-5.3-chat`)
- Synthesis-modell: `../orchestrator/orchestrate.py` (`SYNTH_MODEL`)
- Routing: `../orchestrator/router.py`

Filene beholdes kun som designhistorikk.
