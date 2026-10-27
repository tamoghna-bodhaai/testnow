"""Safe, data-driven graph functions for generated explainer scenes."""
import ast
import math
import operator


OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow,
}
UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
FUNCTIONS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "sqrt": math.sqrt, "log": math.log, "exp": math.exp, "abs": abs,
}
CONSTANTS = {"pi": math.pi, "e": math.e}


def parse_function(expression: str, variable: str):
    """Return a callable after rejecting anything outside basic arithmetic."""
    if variable not in ("x", "y") or not expression or len(expression) > 120:
        raise ValueError("Invalid graph function")
    try:
        root = ast.parse(expression.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid graph function syntax") from exc
    if sum(1 for _ in ast.walk(root)) > 40:
        raise ValueError("Graph function is too complex")

    def check(node):
        if isinstance(node, ast.Expression):
            check(node.body)
        elif isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if not math.isfinite(node.value) or abs(node.value) > 1000:
                raise ValueError("Invalid graph constant")
        elif isinstance(node, ast.Name) and node.id in (variable, *CONSTANTS):
            pass
        elif isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            check(node.left); check(node.right)
        elif isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
            check(node.operand)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and len(node.args) == 1 and not node.keywords:
            check(node.args[0])
        else:
            raise ValueError("Unsupported graph function")

    check(root)

    def evaluate(node, value):
        if isinstance(node, ast.Expression):
            return evaluate(node.body, value)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return value if node.id == variable else CONSTANTS[node.id]
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left, value), evaluate(node.right, value)
            if isinstance(node.op, ast.Pow) and abs(right) > 8:
                raise ValueError("Graph exponent is too large")
            return OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            return UNARY[type(node.op)](evaluate(node.operand, value))
        return FUNCTIONS[node.func.id](evaluate(node.args[0], value))

    return lambda value: evaluate(root, value)


def sample_function(expression: str, variable: str, start: float, end: float, count: int = 241):
    """Return continuous runs of actual (x, y) values, split at undefined points."""
    if not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise ValueError("Invalid graph domain")
    fn = parse_function(expression, variable)
    runs, current = [], []
    for index in range(count):
        value = start + (end - start) * index / (count - 1)
        try:
            result = fn(value)
            if isinstance(result, complex) or not math.isfinite(result) or abs(result) > 1000:
                raise ValueError("Undefined graph value")
        except (ArithmeticError, OverflowError, ValueError, TypeError):
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        point = (value, float(result)) if variable == "x" else (float(result), value)
        # Do not draw a false bridge across a pole such as 1/x or tan(x).
        if current:
            previous = current[-1][1 if variable == "x" else 0]
            if previous * result < 0 and abs(previous - result) > max(10, 1.5 * max(abs(previous), abs(result))):
                if len(current) >= 2:
                    runs.append(current)
                current = []
        current.append(point)
    if len(current) >= 2:
        runs.append(current)
    if not runs:
        raise ValueError("Graph function has no visible points in the domain")
    return runs


def sample_region(upper: str, lower: str, variable: str, start: float, end: float, count: int = 161):
    """Sample a filled region between two actual functions on one domain."""
    if not math.isfinite(start) or not math.isfinite(end) or start >= end or count < 3:
        raise ValueError("Invalid region domain")
    high, low = parse_function(upper, variable), parse_function(lower, variable)
    upper_points, lower_points = [], []
    has_area = False
    for index in range(count):
        value = start + (end - start) * index / (count - 1)
        try:
            top, bottom = float(high(value)), float(low(value))
        except (ArithmeticError, OverflowError, ValueError, TypeError) as exc:
            raise ValueError("Region boundary is undefined on its domain") from exc
        if not all(math.isfinite(v) and abs(v) <= 1000 for v in (top, bottom)):
            raise ValueError("Region boundary is undefined on its domain")
        if top < bottom - 1e-7:
            raise ValueError("Region boundaries cross; split the region at the intersection")
        has_area |= top > bottom + 1e-7
        if variable == "x":
            upper_points.append((value, top))
            lower_points.append((value, bottom))
        else:
            upper_points.append((top, value))
            lower_points.append((bottom, value))
    if not has_area:
        raise ValueError("Region has no visible area")
    return upper_points + lower_points[::-1]
