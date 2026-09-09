"""Bounded, exact replay of operational hypotheses; never executes generated code.

A specification defines the claim checked by this module. Its alignment with a
natural-language scientific objective still needs review. Passing a finite window
is computational evidence, not a theorem or a novelty certificate.
"""
from __future__ import annotations

import ast
import itertools
import math
import operator
from fractions import Fraction
from typing import Any

from lab.integrity import content_fingerprint
from lab.tools import ToolResult

VERSION = 1
MAX_POINTS = 10000
MAX_WORK = 1000000
FUNCTIONS = {"abs", "min", "max", "gcd", "ceil_log2", "collatz_steps", "residue", "collatz_prefix_lower"}
BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
CMPOPS = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
          ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}


class CheckError(ValueError):
    pass


def integer(value: Any) -> int:
    if type(value) is not int or abs(value) > 10**12:
        raise CheckError("Expected an integer with absolute value <= 10^12")
    return value


def expression(text: Any, variables: set[str], *, predicate: bool = True) -> ast.AST:
    if not isinstance(text, str) or not text.strip() or len(text) > 1500:
        raise CheckError("Expression must be a nonempty string of at most 1500 characters")
    try:
        tree = ast.parse(text, mode="eval").body
    except (SyntaxError, RecursionError) as exc:
        raise CheckError("Invalid expression") from exc
    allowed = (ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp, ast.UAdd, ast.USub,
               ast.Compare, ast.BoolOp, ast.And, ast.Or, ast.Not, ast.Call, *BINOPS, *CMPOPS)
    nodes = list(ast.walk(tree))
    if len(nodes) > 200:
        raise CheckError("Expression is too complex")
    for node in nodes:
        if not isinstance(node, allowed):
            raise CheckError(f"Unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in variables | FUNCTIONS:
            raise CheckError(f"Unknown symbol: {node.id}; generated definitions are not trusted functions")
        if isinstance(node, ast.Constant):
            integer(node.value)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS
            or node.keywords or not 1 <= len(node.args) <= 3
        ):
            raise CheckError("Unsupported function call")
    if predicate and not isinstance(tree, (ast.Compare, ast.BoolOp, ast.UnaryOp)):
        raise CheckError("Predicate must be a Boolean comparison")
    return tree


def normalize_spec(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"variables", "assumptions", "predicate"}:
        raise CheckError("claim_spec requires exactly variables, assumptions, predicate")
    variables = raw["variables"]
    if not isinstance(variables, dict) or not 1 <= len(variables) <= 4:
        raise CheckError("Declare between one and four integer variables")
    domain = {}
    for name, bounds in sorted(variables.items()):
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_") or name in FUNCTIONS:
            raise CheckError("Invalid variable name")
        if not isinstance(bounds, dict) or set(bounds) != {"min", "max"}:
            raise CheckError("Every variable requires min and max (null means unbounded above)")
        low = integer(bounds["min"])
        high = None if bounds["max"] is None else integer(bounds["max"])
        if high is not None and high < low:
            raise CheckError("Empty variable domain")
        domain[name] = {"min": low, "max": high}
    assumptions = raw["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > 8:
        raise CheckError("assumptions must be a list with at most eight predicates")
    names = set(domain)
    return {"variables": domain,
            "assumptions": [ast.unparse(expression(s, names)) for s in assumptions],
            "predicate": ast.unparse(expression(raw["predicate"], names))}


def spec_hash(spec: dict[str, Any]) -> str:
    return content_fingerprint(f"operational-claim:v{VERSION}", normalize_spec(spec))


class Evaluator:
    def __init__(self):
        self.work = 0

    def tick(self) -> None:
        self.work += 1
        if self.work > MAX_WORK:
            raise CheckError("Replay work budget exceeded; unresolved, not false")

    def call(self, name: str, args: list[Any]) -> Any:
        if name in {"abs", "min", "max"}:
            if name == "abs":
                if len(args) != 1:
                    raise CheckError("abs needs one argument")
                return abs(args[0])
            return min(args) if name == "min" else max(args)
        if any(isinstance(v, bool) or not isinstance(v, (int, Fraction)) or int(v) != v for v in args):
            raise CheckError("This function requires integer arguments")
        values = [integer(int(v)) for v in args]
        if name == "gcd" and len(values) == 2:
            return math.gcd(*values)
        if name == "ceil_log2" and len(values) == 1 and values[0] >= 1:
            return (values[0] - 1).bit_length()
        if name in {"residue", "collatz_prefix_lower"} and len(values) == 2:
            n, k = values
            if n < 1 or not 1 <= k <= 32:
                raise CheckError("Expected positive n and 1 <= k <= 32")
            if name == "residue":
                return n % (1 << k) or (1 << k)
            lower = 0
            for t in range(1, k + 1):
                self.tick()
                n = n // 2 if n % 2 == 0 else 3 * n + 1
                if n < 2:
                    break
                lower = t
            return lower
        if name == "collatz_steps" and len(values) == 1 and values[0] >= 1:
            n = values[0]
            for steps in range(10001):
                self.tick()
                if n == 1:
                    return steps
                n = n // 2 if n % 2 == 0 else 3 * n + 1
                if n.bit_length() > 256:
                    break
            raise CheckError("Orbit unresolved within step budget")
        raise CheckError(f"Invalid arguments for {name}")

    def evaluate(self, node: ast.AST, env: dict[str, int]) -> Any:
        self.tick()
        if isinstance(node, ast.Constant):
            return Fraction(integer(node.value))
        if isinstance(node, ast.Name) and node.id in env:
            return Fraction(env[node.id])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            value = self.call(node.func.id, [self.evaluate(a, env) for a in node.args])
        elif isinstance(node, ast.BinOp):
            left, right = self.evaluate(node.left, env), self.evaluate(node.right, env)
            if isinstance(node.op, ast.Pow) and (int(right) != right or not 0 <= right <= 32):
                raise CheckError("Exponent must be an integer in [0,32]")
            value = BINOPS[type(node.op)](left, int(right) if isinstance(node.op, ast.Pow) else right)
        elif isinstance(node, ast.Compare):
            left = self.evaluate(node.left, env)
            for op, rhs in zip(node.ops, node.comparators):
                right = self.evaluate(rhs, env)
                if not CMPOPS[type(op)](left, right):
                    return False
                left = right
            return True
        elif isinstance(node, ast.BoolOp):
            values = (self.boolean(n, env) for n in node.values)
            return all(values) if isinstance(node.op, ast.And) else any(values)
        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return not self.boolean(node.operand, env)
            value = self.evaluate(node.operand, env)
            value = -value if isinstance(node.op, ast.USub) else value
        else:
            raise CheckError("Invalid expression node")
        if not isinstance(value, (int, Fraction)) or isinstance(value, bool):
            raise CheckError("Expected exact arithmetic value")
        value = Fraction(value)
        if max(value.numerator.bit_length(), value.denominator.bit_length()) > 256:
            raise CheckError("Arithmetic size budget exceeded")
        return value

    def boolean(self, node: ast.AST, env: dict[str, int]) -> bool:
        result = self.evaluate(node, env)
        if type(result) is not bool:
            raise CheckError("Expected Boolean predicate, not truthy arithmetic")
        return result


def check_claim(spec: dict[str, Any], request: dict[str, Any], *, item_id: str,
                claim_hash: str, iteration: int) -> ToolResult:
    metadata: dict[str, Any] = {"item_id": item_id, "claim_hash": claim_hash, "iteration": iteration,
                                "checker_version": VERSION, "claim_replayed": False}
    try:
        spec = normalize_spec(spec)
        metadata["claim_spec_hash"] = spec_hash(spec)
        if "claim_spec" in request and spec_hash(request["claim_spec"]) != metadata["claim_spec_hash"]:
            raise CheckError("Request attempted to replace the frozen predicate")
        variables = spec["variables"]
        witness = request.get("witness")
        scope = request.get("scope") or variables
        if witness is not None:
            if not isinstance(witness, dict) or set(witness) != set(variables):
                raise CheckError("Witness must bind every variable exactly")
            scope = {name: {"min": integer(v), "max": integer(v)} for name, v in witness.items()}
        if not isinstance(scope, dict) or set(scope) != set(variables):
            raise CheckError("Scope must bind every variable exactly")
        ranges = []
        normalized_scope = {}
        points = 1
        for name, bounds in variables.items():
            entry = scope[name]
            if not isinstance(entry, dict) or set(entry) != {"min", "max"}:
                raise CheckError("Scope bounds require min and max")
            low, high = integer(entry["min"]), integer(entry["max"])
            if high < low or low < bounds["min"] or (bounds["max"] is not None and high > bounds["max"]):
                raise CheckError("Scope/witness outside frozen domain")
            points *= high - low + 1
            if points > MAX_POINTS:
                raise CheckError("Scope exceeds 10000 points; request a smaller window")
            ranges.append(range(low, high + 1))
            normalized_scope[name] = {"min": low, "max": high}
        metadata["covered_scope"] = normalized_scope
        evaluator = Evaluator()
        assumptions = [expression(s, set(variables)) for s in spec["assumptions"]]
        predicate = expression(spec["predicate"], set(variables))
        checked = 0
        for values in itertools.product(*ranges):
            env = dict(zip(variables, values))
            if not all(evaluator.boolean(a, env) for a in assumptions):
                continue
            checked += 1
            if not evaluator.boolean(predicate, env):
                metadata.update(claim_replayed=True, kind="DETERMINISTIC_COUNTEREXAMPLE", witness=env,
                                checked_points=checked, exhaustive=False)
                return ToolResult(True, "claim_check", output=f"Frozen predicate is false at {env}", metadata=metadata)
        if not checked:
            raise CheckError("No admissible point was checked; vacuous pass rejected")
        if witness is not None:
            raise CheckError("Proposed counterexample satisfies the original predicate; refutation rejected")
        metadata.update(claim_replayed=True, kind="EXACT_PASS", checked_points=checked,
                        exhaustive=normalized_scope == variables)
        return ToolResult(True, "claim_check", output=f"Predicate passed at {checked} admissible points; finite computation only.", metadata=metadata)
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError, RecursionError) as exc:
        metadata.update(kind="INCONCLUSIVE", status="CLAIM_CHECK_REJECTED")
        return ToolResult(False, "claim_check", error=str(exc), metadata=metadata)
