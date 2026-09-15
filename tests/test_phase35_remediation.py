from pathlib import Path
import subprocess

from agent_system.cli import build_reviewer
from agent_system.context import ContextBuilder
from agent_system.models import TaskStatus
from agent_system.orchestrator import Orchestrator
from agent_system.providers import ProviderAvailability, ProviderRegistry
from agent_system.providers.base import ExecutionResult, ModelProvider, ProviderCapabilities, ProviderTier
from agent_system.repair import RepairLoop
from agent_system.reviewer import ProviderReviewer, Reviewer, ReviewVerdict
from agent_system.router import ModelRouter
from agent_system.scope import ScopeGate
from agent_system.verifier import VerificationReport, VerificationResult, Verifier


class StaticReviewerProvider(ModelProvider):
    def __init__(self):
        super().__init__("review", "codex-reviewer", ProviderTier.PREMIUM, {})
        self.called = False
    def availability(self): return ProviderAvailability.AVAILABLE
    def capabilities(self): return ProviderCapabilities()
    def execute(self, task, context, worktree):
        self.called = True
        return ExecutionResult(True, '{"verdict":"PASS","notes":"Reviewed","details":{}}', modified_files=[], metadata={"review":{"verdict":"PASS","notes":"Reviewed","details":{}}})
    def classify_error(self, error): return ProviderAvailability.PROVIDER_ERROR


def test_provider_reviewer_wired():
    registry = ProviderRegistry(); provider = StaticReviewerProvider(); registry.register(provider)
    reviewer = build_reviewer(registry, Path("."))
    assert isinstance(reviewer, ProviderReviewer)


def test_ai_pass_cannot_override_failed_verification(tmp_path):
    failed = VerificationResult("test", str(tmp_path), 1, "", "failed", 1, False, "FAIL")
    report = VerificationReport("TASK-X", "low", None, None, [failed], "FAIL", "now")
    result = ProviderReviewer(StaticReviewerProvider(), tmp_path).review("TASK-X", "goal", "low", "+bad", report)
    assert result.verdict == ReviewVerdict.FAIL_TESTS
    assert result.details.get("ai_verdict_overridden") is True


def test_scope_gate_preserves_preexisting_original_changes(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git","init",str(repo)],check=True,capture_output=True)
    subprocess.run(["git","-C",str(repo),"config","user.email","t@t"],check=True)
    subprocess.run(["git","-C",str(repo),"config","user.name","T"],check=True)
    (repo/"a.txt").write_text("a\n"); subprocess.run(["git","-C",str(repo),"add","."],check=True); subprocess.run(["git","-C",str(repo),"commit","-m","init"],check=True,capture_output=True)
    wt=tmp_path/"wt"; subprocess.run(["git","-C",str(repo),"worktree","add","-b","agent/t",str(wt),"HEAD"],check=True,capture_output=True)
    (repo/"local.txt").write_text("keep\n")
    gate=ScopeGate(wt,[],[],repo); gate.record_baseline(); (wt/"a.txt").write_text("changed\n")
    assert gate.check().passed
    assert (repo/"local.txt").read_text()=="keep\n"


def test_symlink_escape_is_blocked(tmp_path):
    wt=tmp_path/"wt"; wt.mkdir(); outside=tmp_path/"outside"; outside.write_text("x")
    link=wt/"link"; link.symlink_to(outside)
    assert ScopeGate(wt,[],[]).validate_path_containment(link) is not None


def test_repair_e2e_reverifies_and_keeps_original_repo(tmp_path):
    repo=tmp_path/"repo"; repo.mkdir(); subprocess.run(["git","init",str(repo)],check=True,capture_output=True)
    subprocess.run(["git","-C",str(repo),"config","user.email","t@t"],check=True); subprocess.run(["git","-C",str(repo),"config","user.name","T"],check=True)
    (repo/"calc.py").write_text("def add(a,b):\n    return a-b\n"); subprocess.run(["git","-C",str(repo),"add","."],check=True); subprocess.run(["git","-C",str(repo),"commit","-m","init"],check=True,capture_output=True)
    wt=tmp_path/"wt"; subprocess.run(["git","-C",str(repo),"worktree","add","-b","agent/r",str(wt),"HEAD"],check=True,capture_output=True); (wt/"calc.py").write_text("def add(a,b):\n    return a*b\n")
    projects=tmp_path/"projects"; projects.mkdir(); (projects/"p.yaml").write_text(f'name: p\nrepository:\n  path: {repo}\ncommands:\n  test: "python -B -c \'import calc; assert calc.add(2,3)==5\'"\nprotected_paths: []\nrisk_rules: {{}}\n')
    orch=Orchestrator(tmp_path); orch.state.create_task("TASK-R","p","fix","low"); orch.state.update_task("TASK-R",status=TaskStatus.REVIEWING.value,branch="agent/r",worktree=str(wt),provider_id="repair")
    class Repair(ModelProvider):
        def __init__(self): super().__init__("repair","repair",ProviderTier.FREE,{})
        def availability(self): return ProviderAvailability.AVAILABLE
        def capabilities(self): return ProviderCapabilities(True,True)
        def execute(self,task,context,worktree): (worktree/"calc.py").write_text("def add(a,b):\n    return a+b\n"); return ExecutionResult(True,"fixed",modified_files=["calc.py"])
        def classify_error(self,error): return ProviderAvailability.PROVIDER_ERROR
    registry=ProviderRegistry(); registry.register(Repair()); routing=tmp_path/"routing.yaml"; routing.write_text("low:\n  - repair\n")
    project=orch._load_project("p"); verifier=Verifier(project,wt,repo); reviewer=Reviewer("deterministic")
    initial=verifier.run("TASK-R","low",[]); initial_review=reviewer.review("TASK-R","fix","low",ScopeGate(wt,[],[],repo).get_diff(),initial)
    result=RepairLoop(orch,ModelRouter(registry,routing),ContextBuilder(),verifier,reviewer).run("TASK-R",ScopeGate(wt,[],[],repo).get_diff(),initial,initial_review)
    assert result.success and result.final_verification.overall_status=="PASS"
    assert (repo/"calc.py").read_text()=="def add(a,b):\n    return a-b\n"
