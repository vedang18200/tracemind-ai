from datetime import datetime, timezone

from tracemind.schemas import Finding, Source
from tracemind.state import new_state


def state_with_findings():
    s = new_state("t", "u", "compare platforms")
    s["sources"] = [Source(id="src-1", url="https://example.test/acme", title="Acme",
                           retrieved_at=datetime.now(timezone.utc), content_hash="h")]
    s["findings"] = [
        Finding(id="f1", claim="Acme Pro is $99/month", source_id="src-1",
                excerpt="Pro plan: $99/month", confidence=0.9),
        Finding(id="f2", claim="Acme has scoring", source_id="src-1",
                excerpt="Features: async video, scoring", confidence=0.8),
    ]
    return s