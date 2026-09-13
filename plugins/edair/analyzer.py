"""Structural analysis over a mapped netlist.

Ported from the frozen v1 implementation.  This is the *structural* half:
reachability, paths, cuts, combinational depth, logic cones and clock domains.
The boolean half of v1's analyzer -- expression extraction, truth tables and
signal equivalence -- is deliberately not carried across yet; it is a separate
capability with its own cost, and nothing in the current product path depends on
it.  Leaving it out means an index that says less, not an index that guesses.

One property of the graph is worth stating because it explains most of the
numbers this module produces: **flip-flops are not edges.**  A DFF's Q net is
recorded as driven by the DFF, but the DFF itself is not added to the forward or
backward edge sets.  Every path and every depth here is therefore
*combinational*, and a register boundary is a natural end of a cone rather than
something the traversal has to special-case.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from netlist import Edge, Instance, Netlist, parse_verilog_netlist

#: Depth stands in for "unreachable" inside the memoised search, so it must be
#: more negative than any real depth could reach.
UNREACHABLE_DEPTH = -(10 ** 9)


@dataclass
class PathResult:
    nodes: list[str]
    instances: list[str]


class NetlistAnalyzer:
    def __init__(self, netlist: Netlist):
        self.netlist = netlist
        self.forward_edges: dict[str, list[Edge]] = {}
        self.backward_edges: dict[str, list[Edge]] = {}
        self.signal_drivers: dict[str, Instance] = {}
        self.dff_by_name: dict[str, Instance] = {}
        self._build_graph()

    # -- graph ------------------------------------------------------------

    def _build_graph(self) -> None:
        for inst in self.netlist.instances:
            if inst.is_dff:
                # A register drives its Q but does not conduct a combinational
                # path, which is what keeps every depth below combinational.
                self.dff_by_name[inst.name] = inst
                q = inst.dff_q()
                if q:
                    self.signal_drivers[q] = inst
                continue
            if not inst.is_combinational or not inst.output:
                continue
            self.signal_drivers[inst.output] = inst
            for src in inst.inputs:
                if src.startswith("1'b"):
                    continue
                edge = Edge(src=src, dst=inst.output, instance=inst)
                self.forward_edges.setdefault(src, []).append(edge)
                self.backward_edges.setdefault(inst.output, []).append(edge)

    def _ensure_signal(self, token: str) -> str:
        """Resolve a name, or refuse it.

        Silently treating an unknown name as a signal would let a typo produce an
        empty result that looks like a real answer.
        """
        resolved = self.netlist.resolve_signal_or_instance(token)
        if not resolved:
            raise ValueError(f"unknown signal or instance: {token}")
        return resolved

    # -- reachability -----------------------------------------------------

    def _signals_that_can_reach(self, dst: str, *, blocked: set[str],
                                source: str) -> set[str]:
        seen = {dst}
        stack = [dst]
        while stack:
            node = stack.pop()
            for edge in self.backward_edges.get(node, []):
                if edge.src in blocked and edge.src != source:
                    continue
                if edge.src in seen:
                    continue
                seen.add(edge.src)
                stack.append(edge.src)
        return seen

    def _primary_inputs_reaching(self, signal: str) -> set[str]:
        seen: set[str] = set()
        inputs: set[str] = set()
        stack = [signal]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            if node in self.netlist.inputs:
                inputs.add(node)
            for edge in self.backward_edges.get(node, []):
                if edge.src not in seen:
                    stack.append(edge.src)
        return inputs

    def _is_primary_output_signal(self, signal: str) -> bool:
        """A bus bit counts as an output when its base name is one.

        A mapped netlist names outputs per bit -- ``q[0]``, ``q[1]`` -- while the
        declaration lists ``q``, so a plain membership test would find no
        primary outputs at all in an ordinary design.
        """
        if signal in self.netlist.outputs:
            return True
        if "[" in signal and signal.endswith("]"):
            return signal.split("[", 1)[0] in self.netlist.outputs
        return False

    def _primary_outputs_reachable_from(self, signal: str,
                                        blocked: Optional[set[str]] = None
                                        ) -> set[str]:
        blocked = blocked or set()
        seen: set[str] = set()
        outputs: set[str] = set()
        stack = [signal]
        while stack:
            node = stack.pop()
            if node in seen or node in blocked:
                continue
            seen.add(node)
            if self._is_primary_output_signal(node):
                outputs.add(node)
            for edge in self.forward_edges.get(node, []):
                if edge.dst not in seen and edge.dst not in blocked:
                    stack.append(edge.dst)
        return outputs

    def signal_is_cut_between_any_pi_po(self, signal: str) -> bool:
        """Whether removing this signal disconnects some input from some output.

        A signal that is such a cut is a candidate for being a design's real
        bottleneck; one that is not cannot be, whatever its fan-out.
        """
        cut = self._ensure_signal(signal)
        sources = self._primary_inputs_reaching(cut)
        destinations = self._primary_outputs_reachable_from(cut)
        if not sources or not destinations:
            return False
        for source in sources:
            without_cut = self._primary_outputs_reachable_from(
                source, blocked={cut})
            if any(destination not in without_cut for destination in destinations):
                return True
        return False

    # -- paths ------------------------------------------------------------

    def find_path(self, src: str, dst: str,
                  avoid: Optional[Iterable[str]] = None) -> Optional[PathResult]:
        """One combinational path, or None.

        The reachability pre-pass is what keeps this cheap: without it the search
        explores every branch that cannot possibly arrive.
        """
        source = self._ensure_signal(src)
        destination = self._ensure_signal(dst)
        blocked = {self._ensure_signal(item) for item in (avoid or ())}
        reachable = self._signals_that_can_reach(
            destination, blocked=blocked, source=source)
        if source not in reachable:
            return None
        stack = [(source, [source], [])]
        visited: set[str] = set()
        while stack:
            node, path_nodes, path_instances = stack.pop()
            if node == destination:
                return PathResult(nodes=path_nodes, instances=path_instances)
            if node in visited:
                continue
            visited.add(node)
            for edge in self.forward_edges.get(node, []):
                if edge.dst not in reachable:
                    continue
                if edge.dst in blocked and edge.dst != destination:
                    continue
                if edge.dst in path_nodes:
                    continue
                stack.append((edge.dst, path_nodes + [edge.dst],
                              path_instances + [edge.instance.name]))
        return None

    def all_paths_pass_through(self, src: str, dst: str,
                               must_pass: str) -> tuple[bool, Optional[PathResult]]:
        """Whether every path goes through a signal.

        Answered by looking for a witness that avoids it: one counterexample is
        enough to say no, and the witness is evidence rather than a boolean.
        """
        witness = self.find_path(src, dst, avoid={must_pass})
        return (witness is None, witness)

    def iter_paths(self, src: str, dst: str, *, limit: Optional[int] = None,
                   avoid: Optional[Iterable[str]] = None):
        source = self._ensure_signal(src)
        destination = self._ensure_signal(dst)
        blocked = {self._ensure_signal(item) for item in (avoid or ())}
        reachable = self._signals_that_can_reach(
            destination, blocked=blocked, source=source)
        if source not in reachable:
            return
        yielded = 0
        stack = [(source, [source], [])]
        while stack and (limit is None or yielded < limit):
            node, path_nodes, path_instances = stack.pop()
            if node == destination:
                yielded += 1
                yield PathResult(nodes=path_nodes, instances=path_instances)
                continue
            for edge in self.forward_edges.get(node, []):
                if edge.dst not in reachable:
                    continue
                if edge.dst in blocked and edge.dst != destination:
                    continue
                if edge.dst in path_nodes:
                    continue
                stack.append((edge.dst, path_nodes + [edge.dst],
                              path_instances + [edge.instance.name]))

    def all_paths(self, src: str, dst: str, *, limit: Optional[int] = None,
                  avoid: Optional[Iterable[str]] = None) -> list[PathResult]:
        """Every path, or the first ``limit`` of them.

        Unbounded enumeration is exponential on a real netlist, so the caller is
        expected to pass a limit; the return value does not claim completeness
        when one was given.
        """
        return list(self.iter_paths(src, dst, limit=limit, avoid=avoid))

    # -- depth and cones --------------------------------------------------

    def max_logic_depth(self, src: str, dst: str) -> tuple[int, PathResult]:
        """The longest combinational path, counted in edges.

        Raises when there is no path.  Returning a depth of zero would be
        indistinguishable from "the source is the destination", and the caller
        deciding what to conclude from "no path" is the whole point.
        """
        source = self._ensure_signal(src)
        destination = self._ensure_signal(dst)
        memo: dict[str, tuple[int, Optional[PathResult]]] = {}

        def dfs(node: str, stack: set[str]) -> tuple[int, Optional[PathResult]]:
            if node == source:
                return (0, PathResult(nodes=[source], instances=[]))
            if node in memo:
                return memo[node]
            if node in stack:
                # A combinational cycle has no finite depth.  It is reported as
                # unreachable rather than recursed into forever.
                return (UNREACHABLE_DEPTH, None)
            stack.add(node)
            best_depth = UNREACHABLE_DEPTH
            best_path: Optional[PathResult] = None
            for edge in self.backward_edges.get(node, []):
                sub_depth, sub_path = dfs(edge.src, stack)
                if sub_path is None:
                    continue
                if sub_depth + 1 > best_depth:
                    best_depth = sub_depth + 1
                    best_path = PathResult(
                        nodes=sub_path.nodes + [node],
                        instances=sub_path.instances + [edge.instance.name],
                    )
            stack.discard(node)
            memo[node] = (best_depth, best_path)
            return memo[node]

        depth, path = dfs(destination, set())
        if depth < 0 or path is None:
            raise ValueError(
                f"no combinational path from {source} to {destination}"
            )
        return depth, path

    def cone_instances(self, output_signal: str) -> set[str]:
        """Every instance feeding a signal, transitively."""
        signal = self._ensure_signal(output_signal)
        seen_signals: set[str] = set()
        seen_instances: set[str] = set()
        stack = [signal]
        while stack:
            node = stack.pop()
            if node in seen_signals:
                continue
            seen_signals.add(node)
            for edge in self.backward_edges.get(node, []):
                seen_instances.add(edge.instance.name)
                if edge.src not in seen_signals:
                    stack.append(edge.src)
        return seen_instances

    def outputs_with_cone_over(self, threshold: int) -> list[tuple[str, int]]:
        """Outputs whose logic cone exceeds a size, largest first.

        The tie-break on the output name makes the order deterministic, so two
        runs over the same netlist produce the same list.
        """
        results = []
        for output in self.netlist.outputs:
            count = len(self.cone_instances(output))
            if count > threshold:
                results.append((output, count))
        return sorted(results, key=lambda item: (-item[1], item[0]))

    # -- clock domains ----------------------------------------------------

    def same_clock_domain(self, dff_a: str, dff_b: str) -> tuple[bool, str, str]:
        """Whether two registers share a clock net.

        A register with no resolvable clock compares as ``'<unknown>'``, which
        makes two such registers look like the same domain.  That is deliberate
        and it is why the two clock names are returned alongside the boolean: a
        reader can see that the answer rests on an unknown rather than being
        told only "yes".
        """
        inst_a = self.netlist.get_instance(dff_a)
        inst_b = self.netlist.get_instance(dff_b)
        if not inst_a or not inst_a.is_dff:
            raise ValueError(f"{dff_a} is not a DFF instance")
        if not inst_b or not inst_b.is_dff:
            raise ValueError(f"{dff_b} is not a DFF instance")
        clock_a = inst_a.dff_clock() or "<unknown>"
        clock_b = inst_b.dff_clock() or "<unknown>"
        return (clock_a == clock_b, clock_a, clock_b)


def summarize_netlist(path: str | Path) -> dict:
    """The netlist's shape, as an index.

    The deepest input-to-output path is reported so a reader can see which pair
    it came from.  A single number would invite the conclusion that the design's
    depth is that value, when it is only the largest pair found.
    """
    netlist = parse_verilog_netlist(path)
    analyzer = NetlistAnalyzer(netlist)
    counts = Counter(instance.cell_type.upper() for instance in netlist.instances)

    max_depth = 0
    max_path = None
    for source in netlist.inputs:
        for destination in netlist.outputs:
            try:
                depth, path_result = analyzer.max_logic_depth(source, destination)
            except ValueError:
                # No combinational path between this pair.  That is an ordinary
                # fact about a netlist, not an error.
                continue
            if depth > max_depth:
                max_depth = depth
                max_path = {
                    "source": source,
                    "destination": destination,
                    "signals": path_result.nodes,
                    "instances": path_result.instances,
                }

    return {
        "module": netlist.module_name,
        "inputs": sorted(netlist.inputs),
        "outputs": sorted(netlist.outputs),
        "instance_count": len(netlist.instances),
        "cell_types": dict(sorted(counts.items())),
        "dff_count": sum(1 for instance in netlist.instances if instance.is_dff),
        "max_combinational_depth": max_depth,
        "max_depth_path": max_path,
        "parser": "mapped-netlist-v1",
        "loss_manifest": {
            "combinational_only": (
                "depth and paths stop at register boundaries; sequential depth "
                "is not represented"
            ),
            "boolean_analysis": "not performed; no equivalence or truth table",
        },
    }
