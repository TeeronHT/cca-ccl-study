#!/usr/bin/env python3
"""Run the vendored StragglAR power-of-two synthesizer for n=16."""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model


def main():
    document = model.build_stragglar()
    if not model.provenance_ok(document):
        print("synthesized schedule does not allreduce", file=sys.stderr)
        return 1
    out = os.path.join(model.root(), "schedules", "stragglar", "schedule.json")
    with open(out, "w") as handle:
        json.dump(document, handle)
        handle.write("\n")
    digest = model.sha256(out)
    provenance = {
        "source_repository": "https://github.com/arjundevraj/stragglar",
        "source_commit": model.SOURCE_COMMIT,
        "synthesis_command": model.SYNTHESIS,
        "generation_date": datetime.date.today().isoformat(),
        "schedule_sha256": digest,
        "straggler": model.STRAGGLER,
        "completion_rounds": document["completion_rounds"],
        "theoretical_q_res_bytes": document["theoretical_q_res_bytes"],
        "q_res_meaning": document["q_res_meaning"],
    }
    path = os.path.join(model.root(), "schedules", "provenance", "provenance.json")
    with open(path, "w") as handle:
        json.dump(provenance, handle, indent=2)
        handle.write("\n")
    print(
        f"rounds={document['completion_rounds']} "
        f"pre={len(document['precondition'])} "
        f"completion_sends={len(document['completion'])} "
        f"sha256={digest[:12]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
