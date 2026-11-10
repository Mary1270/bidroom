import ast
import io
import os
import tokenize
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "contract.py")
TEXT = open(PATH, encoding="utf-8").read()
LINES = TEXT.splitlines()
HEADER = (
    "# v0.2.16",
    '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
)


class TestStudioFileFormat(unittest.TestCase):
    def test_header_is_exactly_two_lines_then_code(self):
        self.assertEqual(tuple(LINES[:2]), HEADER)
        self.assertEqual(LINES[2], "from genlayer import *")

    def test_no_other_comments(self):
        comments = [t for t in tokenize.generate_tokens(io.StringIO(TEXT).readline) if t.type == tokenize.COMMENT]
        self.assertEqual({c.start[0] for c in comments}, {1, 2})

    def test_no_docstrings_or_bare_strings(self):
        tree = ast.parse(TEXT)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                self.assertIsNone(ast.get_docstring(node, clean=False), getattr(node, "name", "module"))
            if isinstance(node, ast.Expr):
                self.assertFalse(isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))

    def test_plain_ascii_no_tabs_no_trailing_space(self):
        TEXT.encode("ascii")
        self.assertNotIn("\t", TEXT)
        for number, line in enumerate(LINES, 1):
            self.assertEqual(line, line.rstrip(), number)

    def test_no_runs_of_blank_lines(self):
        self.assertNotIn("\n\n\n\n", TEXT)

    def test_no_persian_text_anywhere(self):
        for ch in TEXT:
            self.assertFalse("؀" <= ch <= "ۿ")


class TestNondeterministicBlocksAreSelfContained(unittest.TestCase):
    """The blocks passed to run_nondet_unsafe may only close over plain data.
    Capturing `self` drags the whole contract storage into GenVM's pickling
    and produces an Undetermined result on a real node."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(TEXT)
        cls.cls = next(n for n in cls.tree.body if isinstance(n, ast.ClassDef) and n.name == "BidRoom")

    def test_lambdas_given_to_run_nondet_unsafe_never_touch_self(self):
        found = 0
        for node in ast.walk(self.cls):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "gl.vm.run_nondet_unsafe":
                found += 1
                self.assertEqual(len(node.args), 2)
                self.assertEqual(node.keywords, [])
                for arg in node.args:
                    self.assertIsInstance(arg, ast.Lambda)
                    names = {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}
                    self.assertNotIn("self", names)
                    callee = [n for n in ast.walk(arg) if isinstance(n, ast.Call)][0].func.id
                    self.assertTrue(callee.startswith("_select_") or callee.startswith("_rule_"), callee)
        self.assertEqual(found, 2)

    def test_module_level_nondet_functions_never_use_self_or_storage(self):
        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                self.assertNotIn("self", names, node.name)
                self.assertNotIn("gl.message", ast.unparse(node), node.name)

    def test_validators_read_the_calldata_of_gl_vm_return(self):
        for name in ("_select_validator", "_rule_validator"):
            fn = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            src = ast.unparse(fn)
            self.assertIn("isinstance(leader_result, gl.vm.Return)", src)
            self.assertIn("leader_result.calldata", src)

    def test_model_and_fetch_calls_are_exception_safe_inside_the_blocks(self):
        for name in ("_select_compute", "_rule_compute"):
            fn = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                text = ast.unparse(call.func)
                if text in ("gl.nondet.exec_prompt", "gl.nondet.web.render"):
                    guarded = any(
                        isinstance(t, ast.Try) and any(call is c for c in ast.walk(t)) for t in ast.walk(fn)
                    )
                    self.assertTrue(guarded, f"{name}: {text} is not inside try/except")

    def test_no_payable_method_can_mix_nondet_with_money(self):
        for node in self.cls.body:
            if isinstance(node, ast.FunctionDef):
                decorators = [ast.unparse(d) for d in node.decorator_list]
                if "gl.public.write.payable" in decorators:
                    self.assertNotIn("run_nondet", ast.unparse(node))

    def test_only_withdraw_transfers_gen_out(self):
        users = [n.name for n in self.cls.body if isinstance(n, ast.FunctionDef) and "emit_transfer" in ast.unparse(n)]
        self.assertEqual(users, ["withdraw"])

    def test_withdraw_zeroes_the_ledger_before_the_transfer(self):
        fn = next(n for n in self.cls.body if isinstance(n, ast.FunctionDef) and n.name == "withdraw")
        src = ast.unparse(fn)
        self.assertLess(src.index("pending_withdrawals[key] = u256(0)"), src.index("emit_transfer"))


class TestEveryPublicMethodIsAnnotated(unittest.TestCase):
    def test_public_methods_have_decorators_and_typed_arguments(self):
        tree = ast.parse(TEXT)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BidRoom")
        public = [n for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_") and n.name != "__init__"]
        self.assertGreaterEqual(len(public), 30)
        for fn in public:
            decos = [ast.unparse(d) for d in fn.decorator_list]
            self.assertTrue(any(d.startswith("gl.public") for d in decos), fn.name)
            for arg in fn.args.args[1:]:
                self.assertIsNotNone(arg.annotation, f"{fn.name}.{arg.arg}")
            self.assertIsNotNone(fn.returns, fn.name)

    def test_storage_fields_are_declared_at_class_level_and_never_assigned_containers(self):
        tree = ast.parse(TEXT)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BidRoom")
        annotated = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)}
        self.assertEqual(annotated, {"projects", "project_count", "pending_withdrawals", "pending_total"})
        init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        assigned = {t.attr for n in ast.walk(init) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Attribute)}
        self.assertEqual(assigned, {"project_count", "pending_total"})


if __name__ == "__main__":
    unittest.main()
