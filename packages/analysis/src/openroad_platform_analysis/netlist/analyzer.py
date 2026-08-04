from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple, Union
from .model import Edge, Instance, Netlist

@dataclass
class PathResult:
    nodes: List[str]
    instances: List[str]

class NetlistAnalyzer:

    def __init__(self, netlist, max_truth_table_vars=10):
        self.netlist = netlist
        self.max_truth_table_vars = max_truth_table_vars
        self.forward_edges = {}
        self.backward_edges = {}
        self.signal_drivers = {}
        self.dff_by_name = {}
        self._build_graph()

    def _build_graph(self):
        for inst in self.netlist.instances:
            if inst.is_dff:
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

    def _ensure_signal(self, token):
        resolved = self.netlist.resolve_signal_or_instance(token)
        if not resolved:
            raise ValueError(f'Unknown signal or instance: {token}')
        return resolved

    def find_path(self, src, dst, avoid=None):
        src_sig = self._ensure_signal(src)
        dst_sig = self._ensure_signal(dst)
        blocked = {self._ensure_signal(item) for item in avoid or set()}
        can_reach_dst = self._signals_that_can_reach(dst_sig, blocked=blocked, source=src_sig)
        if src_sig not in can_reach_dst:
            return None
        stack = [(src_sig, [src_sig], [])]
        visited_signals = set()
        while stack:
            node, path_nodes, path_instances = stack.pop()
            if node == dst_sig:
                return PathResult(nodes=path_nodes, instances=path_instances)
            if node in visited_signals:
                continue
            visited_signals.add(node)
            for edge in self.forward_edges.get(node, []):
                if edge.dst not in can_reach_dst:
                    continue
                if edge.dst in blocked and edge.dst != dst_sig:
                    continue
                if edge.dst in path_nodes:
                    continue
                stack.append((edge.dst, path_nodes + [edge.dst], path_instances + [edge.instance.name]))
        return None

    def all_paths_pass_through(self, src, dst, must_pass):
        witness = self.find_path(src, dst, avoid={must_pass})
        return (witness is None, witness)

    def all_paths(self, src, dst, *, limit=None, avoid=None):
        return list(self.iter_paths(src, dst, limit=limit, avoid=avoid))

    def iter_paths(self, src, dst, *, limit=None, avoid=None):
        src_sig = self._ensure_signal(src)
        dst_sig = self._ensure_signal(dst)
        blocked = {self._ensure_signal(item) for item in avoid or set()}
        can_reach_dst = self._signals_that_can_reach(dst_sig, blocked=blocked, source=src_sig)
        if src_sig not in can_reach_dst:
            return
        yielded = 0
        stack = [(src_sig, [src_sig], [])]
        while stack and (limit is None or yielded < limit):
            node, path_nodes, path_instances = stack.pop()
            if node == dst_sig:
                yielded += 1
                yield PathResult(nodes=path_nodes, instances=path_instances)
                continue
            for edge in self.forward_edges.get(node, []):
                if edge.dst not in can_reach_dst:
                    continue
                if edge.dst in blocked and edge.dst != dst_sig:
                    continue
                if edge.dst in path_nodes:
                    continue
                stack.append((edge.dst, path_nodes + [edge.dst], path_instances + [edge.instance.name]))

    def _signals_that_can_reach(self, dst, *, blocked, source):
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

    def signal_is_cut_between_any_pi_po(self, signal):
        cut_signal = self._ensure_signal(signal)
        srcs = self._primary_inputs_reaching(cut_signal)
        dsts = self._primary_outputs_reachable_from(cut_signal)
        if not srcs or not dsts:
            return False
        for src in srcs:
            reachable_without_cut = self._primary_outputs_reachable_from(src, blocked={cut_signal})
            if any((dst not in reachable_without_cut for dst in dsts)):
                return True
        return False

    def _primary_inputs_reaching(self, signal):
        seen = set()
        inputs = set()
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

    def _primary_outputs_reachable_from(self, signal, blocked=None):
        blocked = blocked or set()
        seen = set()
        outputs = set()
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

    def _is_primary_output_signal(self, signal):
        if signal in self.netlist.outputs:
            return True
        if '[' in signal and signal.endswith(']'):
            return signal.split('[', 1)[0] in self.netlist.outputs
        return False

    def max_logic_depth(self, src, dst):
        src_sig = self._ensure_signal(src)
        dst_sig = self._ensure_signal(dst)
        memo = {}

        def dfs(node, stack):
            if node == src_sig:
                return (0, PathResult(nodes=[src_sig], instances=[]))
            if node in memo:
                return memo[node]
            if node in stack:
                return (-10 ** 9, None)
            stack.add(node)
            best_depth = -10 ** 9
            best_path = None
            for edge in self.backward_edges.get(node, []):
                sub_depth, sub_path = dfs(edge.src, stack)
                if sub_path is None:
                    continue
                candidate_depth = sub_depth + 1
                candidate_path = PathResult(nodes=sub_path.nodes + [node], instances=sub_path.instances + [edge.instance.name])
                if candidate_depth > best_depth:
                    best_depth = candidate_depth
                    best_path = candidate_path
            stack.remove(node)
            memo[node] = (best_depth, best_path)
            return memo[node]
        depth, path = dfs(dst_sig, set())
        if depth < 0 or path is None:
            raise ValueError(f'No combinational path from {src_sig} to {dst_sig}')
        return (depth, path)

    def cone_instances(self, output_signal):
        signal = self._ensure_signal(output_signal)
        seen_signals = set()
        seen_instances = set()
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

    def outputs_with_cone_over(self, threshold):
        results = []
        for output in self.netlist.outputs:
            count = len(self.cone_instances(output))
            if count > threshold:
                results.append((output, count))
        return sorted(results, key=lambda item: (-item[1], item[0]))

    def same_clock_domain(self, dff_a, dff_b):
        inst_a = self.netlist.get_instance(dff_a)
        inst_b = self.netlist.get_instance(dff_b)
        if not inst_a or not inst_a.is_dff:
            raise ValueError(f'{dff_a} is not a DFF instance')
        if not inst_b or not inst_b.is_dff:
            raise ValueError(f'{dff_b} is not a DFF instance')
        clk_a = inst_a.dff_clock() or '<unknown>'
        clk_b = inst_b.dff_clock() or '<unknown>'
        return (clk_a == clk_b, clk_a, clk_b)

    def _expression(self, signal, memo, stack):
        signal = self._ensure_signal(signal)
        if signal in memo:
            return memo[signal]
        if signal in stack:
            raise ValueError(f'Combinational cycle detected near {signal}')
        if signal.startswith("1'b"):
            return '1' if signal.endswith('1') else '0'
        if signal in self.netlist.inputs:
            memo[signal] = signal
            return signal
        driver = self.signal_drivers.get(signal)
        if driver is None or driver.is_dff:
            memo[signal] = signal
            return signal
        stack.add(signal)
        args = [self._expression(inp, memo, stack) for inp in driver.inputs]
        stack.remove(signal)
        op = driver.cell_type.lower()
        if op == 'buf':
            expr = args[0]
        elif op == 'not':
            expr = f'(~{args[0]})'
        elif op == 'and':
            expr = f'({args[0]} & {args[1]})'
        elif op == 'or':
            expr = f'({args[0]} | {args[1]})'
        elif op == 'nand':
            expr = f'(~({args[0]} & {args[1]}))'
        elif op == 'nor':
            expr = f'(~({args[0]} | {args[1]}))'
        elif op == 'xor':
            expr = f'({args[0]} ^ {args[1]})'
        elif op == 'xnor':
            expr = f'(~({args[0]} ^ {args[1]}))'
        elif op == 'mux':
            expr = f'(({args[2]} & {args[1]}) | (~{args[2]} & {args[0]}))'
        else:
            expr = signal
        memo[signal] = expr
        return expr

    def boolean_expression(self, signal):
        return self._expression(signal, {}, set())

    def support_vars(self, signal):
        expr = self.boolean_expression(signal)
        vars_found = sorted({token for token in expr.replace('(', ' ').replace(')', ' ').replace('~', ' ').replace('&', ' ').replace('|', ' ').replace('^', ' ').split() if token not in {'0', '1'}})
        return vars_found

    def truth_table(self, signal):
        target = self._ensure_signal(signal)
        variables = self.support_vars(target)
        if len(variables) > self.max_truth_table_vars:
            raise ValueError(f'Formal cone for {signal} is too large for the lightweight checker ({len(variables)} vars > {self.max_truth_table_vars})')
        expr = self.boolean_expression(target)
        values = [self._eval_expr(expr, env) for env in iterate_environments(variables)]
        return (variables, values)

    def evaluate(self, signal, env):
        target = self._ensure_signal(signal)
        expr = self.boolean_expression(target)
        return self._eval_expr(expr, env)

    def equivalent_signals(self, left, right):
        left_sig = self._ensure_signal(left)
        right_sig = self._ensure_signal(right)
        left_vars = set(self.support_vars(left_sig))
        right_vars = set(self.support_vars(right_sig))
        variables = sorted(left_vars | right_vars)
        if len(variables) > self.max_truth_table_vars:
            raise ValueError(f'Combined cone for {left} and {right} is too large for the lightweight checker ({len(variables)} vars > {self.max_truth_table_vars})')
        left_expr = self.boolean_expression(left_sig)
        right_expr = self.boolean_expression(right_sig)
        for env in iterate_environments(variables):
            if self._eval_expr(left_expr, env) != self._eval_expr(right_expr, env):
                return (False, env)
        return (True, None)

    def _eval_expr(self, expr, env):
        transformed = expr
        for name, value in sorted(env.items(), key=lambda item: -len(item[0])):
            transformed = re_sub_identifier(transformed, name, 'True' if value else 'False')
        transformed = transformed.replace('~', ' not ').replace('&', ' and ').replace('|', ' or ').replace('^', ' != ')
        return bool(eval(transformed, {'__builtins__': {}}, {}))

    def signals_and_equivalent_to(self, target, search_limit=64):
        target_signal = self._ensure_signal(target)
        target_expr = self.boolean_expression(target_signal)
        candidate_signals = self.netlist.all_signals()[:search_limit]
        matches = []
        for idx, a in enumerate(candidate_signals):
            if a == target_signal:
                continue
            expr_a = self.boolean_expression(a)
            vars_a = set(self.support_vars(a))
            for b in candidate_signals[idx + 1:]:
                if b == target_signal:
                    continue
                expr_b = self.boolean_expression(b)
                vars_b = set(self.support_vars(b))
                combined_vars = sorted(set(self.support_vars(target_signal)) | vars_a | vars_b)
                if len(combined_vars) > self.max_truth_table_vars:
                    continue
                equivalent = True
                for env in iterate_environments(combined_vars):
                    lhs = self._eval_expr(f'({expr_a} & {expr_b})', env)
                    rhs = self._eval_expr(target_expr, env)
                    if lhs != rhs:
                        equivalent = False
                        break
                if equivalent:
                    matches.append((a, b))
        return matches

    def verify_asserted_only_when(self, signal, required_high, required_low):
        target = self._ensure_signal(signal)
        high = self._ensure_signal(required_high)
        low = self._ensure_signal(required_low)
        combined_vars = sorted(set(self.support_vars(target)) | set(self.support_vars(high)) | set(self.support_vars(low)))
        if len(combined_vars) > self.max_truth_table_vars:
            raise ValueError(f'Formal cone for {signal} is too large for the lightweight checker ({len(combined_vars)} vars > {self.max_truth_table_vars})')
        target_expr = self.boolean_expression(target)
        high_expr = self.boolean_expression(high)
        low_expr = self.boolean_expression(low)
        for env in iterate_environments(combined_vars):
            done_val = self._eval_expr(target_expr, env)
            if not done_val:
                continue
            if self._eval_expr(high_expr, env) and (not self._eval_expr(low_expr, env)):
                continue
            return (False, env)
        return (True, None)

def iterate_environments(variables):
    total = len(variables)
    for mask in range(1 << total):
        env = {}
        for idx, name in enumerate(variables):
            env[name] = bool(mask >> idx & 1)
        yield env

def re_sub_identifier(expr, name, replacement):
    import re
    escaped = re.escape(name)
    return re.sub(f'(?<![A-Za-z0-9_$\\[\\]]){escaped}(?![A-Za-z0-9_$\\[\\]])', replacement, expr)
