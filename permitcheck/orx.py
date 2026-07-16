"""Operations research models for the human-in-the-loop workflow.

The deterministic engine produces findings; humans must review them. Two
classic OR models optimize that scarce reviewer time - directly serving the
challenge's goal of reducing AHJ costs and permit delays:

1. Reviewer-finding assignment  - Hungarian algorithm (Kuhn-Munkres, O(n^3))
   minimizes total assignment cost, where cost = estimated review minutes,
   penalized when the finding's discipline is outside the reviewer's
   qualifications. Reviewers are expanded into capacity slots so workload is
   balanced across the team.

2. Permit queue scheduling - Weighted Shortest Processing Time (WSPT) rule,
   provably optimal for minimizing total weighted completion time on a single
   machine (Smith, 1956), extended to parallel reviewers by earliest-available
   list scheduling. Weights encode housing impact (e.g. dwelling units), so
   housing-supply-critical permits clear the queue sooner. A FIFO baseline is
   computed for comparison.

Both models are exact/greedy-deterministic: same inputs, same plan.
"""

import math

INF = float("inf")

# Deterministic effort model (minutes) per finding, by verdict and severity.
REVIEW_MINUTES = {
    "DOES_NOT_MEET": {"critical": 30, "major": 20, "administrative": 8},
    "UNCERTAIN": {"critical": 30, "major": 25, "administrative": 10},
    "INFO_NOT_AVAILABLE": {"critical": 12, "major": 10, "administrative": 5},
}
MISMATCH_PENALTY = 45  # minutes-equivalent penalty for out-of-discipline review


# --------------------------------------------------------------------- #
# Hungarian algorithm (Kuhn-Munkres), potentials formulation, O(n^3).
# cost: rectangular matrix, rows <= cols (pad externally). Returns list
# where result[row] = assigned column.
# --------------------------------------------------------------------- #
def hungarian(cost):
    n, m = len(cost), len(cost[0])
    assert n <= m, "rows must not exceed columns"
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)      # p[j] = row matched to column j (1-based)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            p[j0] = p[way[j0]]
            j0 = way[j0]
    result = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result


# --------------------------------------------------------------------- #
# Findings extraction from an engine run
# --------------------------------------------------------------------- #
def findings_from_run(run):
    """Flatten a compliance run into reviewable findings with effort estimates."""
    out = []
    for r in run["results"]:
        if not r.get("applicable") or r["verdict"] == "MEETS":
            continue
        minutes = REVIEW_MINUTES.get(r["verdict"], {}).get(r["severity"], 15)
        flagged = [i["element"] for i in r["instances"] if i["verdict"] != "MEETS"]
        out.append({
            "run_id": run["run_id"],
            "application_id": run["application"]["id"],
            "rule_id": r["rule_id"],
            "verdict": r["verdict"],
            "severity": r["severity"],
            "discipline": r["discipline"],
            "elements": flagged,
            "estimated_minutes": minutes + 5 * max(0, len(flagged) - 1),
        })
    return out


# --------------------------------------------------------------------- #
# Model 1: reviewer-finding assignment
# --------------------------------------------------------------------- #
def assign_findings(findings, reviewers):
    """Optimal assignment of findings to reviewers (capacity-balanced).

    reviewers: [{"name": ..., "disciplines": ["fire_safety", ...]}, ...]
    Returns per-reviewer worklists plus the total optimized cost.
    """
    if not findings or not reviewers:
        return {"assignments": {r["name"]: [] for r in reviewers}, "total_minutes": 0,
                "method": "hungarian"}

    capacity = math.ceil(len(findings) / len(reviewers))
    slots = [r for r in reviewers for _ in range(capacity)]  # reviewer slots

    cost = []
    for f in findings:
        row = []
        for slot in slots:
            base = f["estimated_minutes"]
            if f["discipline"] not in slot["disciplines"] and \
                    "any" not in slot["disciplines"]:
                base += MISMATCH_PENALTY
            row.append(float(base))
        cost.append(row)

    result = hungarian(cost)
    assignments = {r["name"]: [] for r in reviewers}
    total = 0.0
    for fi, si in enumerate(result):
        slot = slots[si]
        minutes = cost[fi][si]
        total += minutes
        assignments[slot["name"]].append(dict(findings[fi],
                                              assigned_cost_minutes=minutes,
                                              discipline_match=minutes == findings[fi]["estimated_minutes"]))
    for name in assignments:
        assignments[name].sort(key=lambda f: (-{"critical": 2, "major": 1}.get(f["severity"], 0),
                                              f["rule_id"]))
    return {"assignments": assignments, "total_minutes": total,
            "capacity_per_reviewer": capacity, "method": "hungarian"}


# --------------------------------------------------------------------- #
# Model 2: permit queue scheduling (WSPT + list scheduling)
# --------------------------------------------------------------------- #
def schedule_queue(jobs, n_reviewers=1):
    """jobs: [{"application_id", "processing_minutes", "weight"}].

    Returns the WSPT-ordered schedule with start/finish per job and the total
    weighted completion time, compared against a FIFO baseline.
    """
    def simulate(ordered):
        free = [0.0] * max(1, n_reviewers)
        plan, weighted = [], 0.0
        for job in ordered:
            k = min(range(len(free)), key=lambda i: free[i])
            start = free[k]
            finish = start + job["processing_minutes"]
            free[k] = finish
            weighted += job["weight"] * finish
            plan.append(dict(job, reviewer_slot=k + 1,
                             start_minutes=start, finish_minutes=finish))
        return plan, weighted

    fifo_plan, fifo_cost = simulate(jobs)
    wspt = sorted(jobs, key=lambda j: (-j["weight"] / max(j["processing_minutes"], 1e-9),
                                       j["application_id"]))
    plan, cost = simulate(wspt)
    return {
        "method": "WSPT + earliest-available list scheduling",
        "schedule": plan,
        "weighted_completion_minutes": cost,
        "fifo_weighted_completion_minutes": fifo_cost,
        "improvement_pct": round(100.0 * (fifo_cost - cost) / fifo_cost, 1) if fifo_cost else 0.0,
    }


def triage(runs, reviewers):
    """Full triage plan for a set of compliance runs: assignment + schedule."""
    all_findings = []
    jobs = []
    for run in runs:
        findings = findings_from_run(run)
        all_findings.extend(findings)
        minutes = sum(f["estimated_minutes"] for f in findings) or 5
        # weight: housing impact (dwelling units) + critical findings pressure
        units = run.get("_dwelling_units", 1)
        criticals = sum(1 for f in findings if f["severity"] == "critical")
        jobs.append({"application_id": run["application"]["id"],
                     "processing_minutes": minutes,
                     "weight": units + 2 * criticals})
    return {
        "assignment": assign_findings(all_findings, reviewers),
        "queue": schedule_queue(jobs, n_reviewers=len(reviewers) or 1),
        "findings_count": len(all_findings),
    }
