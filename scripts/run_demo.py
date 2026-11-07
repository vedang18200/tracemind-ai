import asyncio
import sys
import time 

from tracemind.graph import build_graph
from tracemind.llm import get_llm
from tracemind.mcp_client import connect_stdio
from tracemind.state import new_state
from tracemind.tools.mcp_tools import researcher_tools

#logging
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.WARNING)

REQUEST = (
    "Research the top five AI interview platforms, compare their pricing and "
    "features, identify market gaps, and generate a competitor analysis report with sources."
)


def show(out):
    print(f"status: {out['status']}  stage: {out['current_stage']}  steps: {out['step_count']}  "
          f"research_rounds: {out['research_rounds']}")
    print(f"sources: {len(out['sources'])}  findings: {len(out['findings'])}  "
          f"verified: {sum(r.status == 'verified' for r in out['verification'])}")
    for e in out["errors"]:
        print(f"  error: {e}")
    print("\n" + (out["report_md"] or "(no report)"))


async def main():
    live = "--live" in sys.argv
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    request = positional[0] if positional else REQUEST
    state = new_state("demo-1", "demo-user", request)

    if live:
    cheap, strong = get_llm(), get_llm(strong=True)
    t0 = time.monotonic()
    async with connect_stdio() as client:
        search, fetch = researcher_tools(client)
        out = await build_graph(cheap, strong_llm=strong, search=search, fetch=fetch).ainvoke(state)
        elapsed = time.monotonic() - t0
        show(out)
        print(f"\nelapsed: {elapsed:.0f}s")
        print(f"cheap  {cheap.model}: {cheap.usage}")
        print(f"strong {strong.model}: {strong.usage}")
        print("\ntool calls:")
        for i in client.invocations:
            print(f"  {i.tool_name:<11} {i.status:<5} {i.duration_ms:>5}ms "
                    f"attempts={i.attempts} {i.error_code or ''}  {i.arguments_summary[:70]}")
    else:
        show(await build_graph(get_llm()).ainvoke(state))


asyncio.run(main())