import asyncio
import sys

from tracemind.graph import build_graph
from tracemind.llm import get_llm
from tracemind.state import new_state

REQUEST = (
    "Research the top five AI interview platforms, compare their pricing and "
    "features, identify market gaps, and generate a competitor analysis report with sources."
)


async def main():
    request = sys.argv[1] if len(sys.argv) > 1 else REQUEST
    out = await build_graph(get_llm()).ainvoke(new_state("demo-1", "demo-user", request))
    
    print(f"sources: {len(out['sources'])}  findings: {len(out['findings'])}  "
      f"verified: {sum(r.status == 'verified' for r in out['verification'])}")

    print(f"status: {out['status']}  stage: {out['current_stage']}  "
          f"steps: {out['step_count']}  research_rounds: {out['research_rounds']}")
    for e in out["errors"]:
        print(f"  error: {e}")
    print("\n" + (out["report_md"] or "(no report)"))


asyncio.run(main())