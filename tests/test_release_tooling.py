"""Tests for the offline release tooling.

These tests exercise the boundaries the release process depends on:

* two builds from the same sources are byte-identical;
* archives carry a normalised timestamp and no bytecode;
* a failed build removes its staging directory and leaves a log;
* stale or modified artifacts are detected and cannot be published;
* publishing without a passing verification report is refused;
* publication and rollback switch atomically;
* the hand-written TOML reader matches the real file.
"""

import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from releasekit import backend, build, config, fsutil, manifest, publish  # noqa: E402
from releasekit import toml_compat  # noqa: E402


class ReleaseToolingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="releasekit-test-"))
        self.addCleanup(fsutil.remove_tree, self.tmp)

    def build(self, build_id):
        return build.build(ROOT, dist_dir=self.tmp / "dist", build_id=build_id)


class TestConfig(ReleaseToolingCase):
    def test_pyproject_is_parsed_without_tomllib(self):
        data = toml_compat.load(ROOT / "pyproject.toml")
        self.assertEqual(data["project"]["name"], "assessment-score-equating")
        self.assertEqual(data["project"]["version"], "0.1.0")
        self.assertEqual(data["tool"]["releasekit"]["supported-pythons"], ["python3.11", "python3.12"])

    def test_handwritten_parser_matches_pyproject(self):
        data = toml_compat.load(ROOT / "pyproject.toml")
        cfg = config.load(ROOT)
        self.assertEqual(data["project"]["version"], cfg.version)
        self.assertEqual(data["project"]["requires-python"], cfg.requires_python)

    def test_at_least_two_runtimes_are_declared(self):
        cfg = config.load(ROOT)
        self.assertGreaterEqual(len(cfg.supported_pythons), 2)
        self.assertEqual(len(set(cfg.supported_pythons)), len(cfg.supported_pythons))


class TestBuild(ReleaseToolingCase):
    def test_build_produces_expected_files_and_manifest(self):
        outcome = self.build("t1")
        names = sorted(path.name for path in outcome.directory.iterdir())
        self.assertEqual(
            names,
            [
                "MANIFEST.json",
                "SHA256SUMS",
                "assessment_score_equating-0.1.0-py3-none-any.whl",
                "assessment_score_equating-0.1.0.tar.gz",
            ],
        )
        self.assertEqual(
            manifest.verify_manifest(outcome.manifest, outcome.directory), []
        )
        sums = (outcome.directory / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), 3)  # two artifacts + manifest

    def test_repeated_builds_are_byte_identical(self):
        first = self.build("r1")
        second = self.build("r2")
        for name in (
            "assessment_score_equating-0.1.0-py3-none-any.whl",
            "assessment_score_equating-0.1.0.tar.gz",
            "MANIFEST.json",
        ):
            self.assertEqual(
                (first.directory / name).read_bytes(),
                (second.directory / name).read_bytes(),
                name,
            )
        comparison = manifest.compare_manifests(first.manifest, second.manifest)
        self.assertTrue(comparison["reproducible"], comparison["explanations"])

    def test_archives_use_a_fixed_timestamp(self):
        outcome = self.build("ts")
        import tarfile

        with zipfile.ZipFile(outcome.directory / "assessment_score_equating-0.1.0-py3-none-any.whl") as zf:
            for info in zf.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0), info.filename)
        with tarfile.open(outcome.directory / "assessment_score_equating-0.1.0.tar.gz") as tf:
            for member in tf.getmembers():
                self.assertEqual(member.mtime, fsutil.FIXED_EPOCH, member.name)
                self.assertEqual(member.uid, 0)
                self.assertEqual(member.uname, "")

    def test_wheel_has_record_and_no_bytecode(self):
        outcome = self.build("rec")
        wheel = outcome.directory / "assessment_score_equating-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
            self.assertIn("assessment_score_equating/data/input.json", names)
            self.assertIn(
                "assessment_score_equating-0.1.0.dist-info/RECORD", names
            )
            record = zf.read("assessment_score_equating-0.1.0.dist-info/RECORD").decode()
            for name in names:
                if name.endswith(".pyc"):
                    self.fail("bytecode packaged: %s" % name)
            for line in record.splitlines():
                entry = line.split(",")[0]
                self.assertIn(entry, names, entry)

    def test_manifest_diff_explains_a_source_change(self):
        first = self.build("d1")
        changed = json.loads(json.dumps(first.manifest))
        for record in changed["sources"]:
            if record["path"] == "src/assessment_score_equating/engine.py":
                record["sha256"] = "0" * 64
        comparison = manifest.compare_manifests(first.manifest, changed)
        self.assertFalse(comparison["reproducible"])
        self.assertIn("inputs changed", " ".join(comparison["explanations"]))
        self.assertEqual(comparison["sources"]["changed"][0]["path"],
                         "src/assessment_score_equating/engine.py")

    def test_failed_build_cleans_staging_and_logs(self):
        def explode(*args, **kwargs):
            raise RuntimeError("simulated backend failure")

        dist = self.tmp / "dist"
        with mock.patch.object(backend, "build_wheel", explode):
            with self.assertRaises(build.BuildError) as caught:
                build.build(ROOT, dist_dir=dist, build_id="boom")
        self.assertIn("simulated backend failure", str(caught.exception))
        self.assertFalse((dist / ".staging-boom").exists())
        self.assertFalse((dist / "0.1.0-boom").exists())
        logs = list((dist / "logs").glob("build-boom.log"))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text()
        self.assertIn("status=failed", text)
        self.assertIn("cleanup=staging removed", text)
        self.assertIn("simulated backend failure", text)

    def test_failed_build_keeps_previous_completed_build(self):
        good = self.build("good")
        dist = self.tmp / "dist"
        original = backend.build_wheel

        def fail_after_sdist(wheel_directory, config_settings=None, metadata_directory=None, root=None):
            raise RuntimeError("wheel step failed")

        with mock.patch.object(backend, "build_wheel", fail_after_sdist):
            with self.assertRaises(build.BuildError):
                build.build(ROOT, dist_dir=dist, build_id="bad")
        self.assertTrue(good.directory.is_dir())
        self.assertTrue((good.directory / "MANIFEST.json").is_file())
        self.assertFalse((dist / "0.1.0-bad").exists())
        self.assertEqual(manifest.verify_manifest(good.manifest, good.directory), [])
        log = (dist / "logs" / "build-bad.log").read_text()
        self.assertIn("wheel step failed", log)
        self.assertNotIn("status=ok", log.split("status=failed")[0])

    def test_clean_removes_interrupted_staging_only(self):
        outcome = self.build("keepme")
        stale = self.tmp / "dist" / ".staging-interrupted"
        stale.mkdir()
        (stale / "partial.whl").write_text("half")
        removed = build.clean(self.tmp / "dist")
        self.assertEqual([path.name for path in removed], [".staging-interrupted"])
        self.assertFalse(stale.exists())
        self.assertTrue(outcome.directory.is_dir())


class TestManifestGuards(ReleaseToolingCase):
    def setUp(self):
        super().setUp()
        self.outcome = self.build("guard")

    def test_modified_artifact_is_detected(self):
        target = self.outcome.directory / "assessment_score_equating-0.1.0-py3-none-any.whl"
        target.write_bytes(target.read_bytes() + b"tampered")
        problems = manifest.verify_manifest(self.outcome.manifest, self.outcome.directory)
        self.assertTrue(any("hash mismatch" in problem for problem in problems), problems)

    def test_stale_artifact_is_detected(self):
        (self.outcome.directory / "assessment_score_equating-0.0.9-py3-none-any.whl").write_bytes(b"old")
        problems = manifest.verify_manifest(self.outcome.manifest, self.outcome.directory)
        self.assertTrue(any("stale" in problem for problem in problems), problems)

    def test_missing_artifact_is_detected(self):
        (self.outcome.directory / "assessment_score_equating-0.1.0.tar.gz").unlink()
        problems = manifest.verify_manifest(self.outcome.manifest, self.outcome.directory)
        self.assertTrue(any("missing artifact" in problem for problem in problems), problems)


class TestPublish(ReleaseToolingCase):
    def setUp(self):
        super().setUp()
        self.outcome = self.build("pub1")
        self.release = self.tmp / "release"

    def _write_report(self, passed=True, version="0.1.0", manifest_sha=None):
        report = {
            "project": "assessment-score-equating",
            "version": version,
            "passed": passed,
            "manifest_sha256": manifest_sha
            or fsutil.sha256_file(self.outcome.directory / "MANIFEST.json"),
        }
        path = self.outcome.directory / "VERIFICATION.json"
        path.write_text(json.dumps(report))
        return path

    def test_publish_requires_verification(self):
        with self.assertRaises(publish.PublishError) as caught:
            publish.publish(ROOT, self.outcome.directory, release_dir=self.release)
        self.assertIn("verification report", str(caught.exception).lower())
        self.assertFalse((self.release / "current").exists())

    def test_publish_refuses_incomplete_verification(self):
        path = self._write_report()
        report = json.loads(path.read_text())
        report["complete"] = False
        path.write_text(json.dumps(report))
        with self.assertRaises(publish.PublishError) as caught:
            publish.publish(ROOT, self.outcome.directory, release_dir=self.release)
        self.assertIn("incomplete", str(caught.exception))
        self.assertFalse((self.release / "current").exists())

    def test_publish_refuses_failed_verification(self):
        self._write_report(passed=False)
        with self.assertRaises(publish.PublishError) as caught:
            publish.publish(ROOT, self.outcome.directory, release_dir=self.release)
        self.assertIn("did not pass", str(caught.exception))
        self.assertFalse((self.release / "current").exists())

    def test_publish_refuses_report_for_other_manifest(self):
        self._write_report(manifest_sha="0" * 64)
        with self.assertRaises(publish.PublishError) as caught:
            publish.publish(ROOT, self.outcome.directory, release_dir=self.release)
        self.assertIn("changed after verification", str(caught.exception))

    def test_publish_refuses_tampered_artifacts_even_with_report(self):
        self._write_report()
        target = self.outcome.directory / "assessment_score_equating-0.1.0-py3-none-any.whl"
        target.write_bytes(target.read_bytes() + b"x")
        with self.assertRaises(publish.PublishError) as caught:
            publish.publish(ROOT, self.outcome.directory, release_dir=self.release)
        self.assertIn("not publishable", str(caught.exception))
        self.assertFalse((self.release / "current").exists())

    def test_publish_then_rollback(self):
        self._write_report()
        first = publish.publish(
            ROOT, self.outcome.directory, release_dir=self.release
        )
        self.assertEqual(first.previous_target, None)
        self.assertEqual(
            (self.release / "current").resolve(),
            (self.release / "releases" / "0.1.0-pub1").resolve(),
        )
        self.assertTrue((self.release / "published" / "MANIFEST.json").is_file())

        second_build = self.build("pub2")
        original = self.outcome
        self.outcome = second_build
        self._write_report()
        second = publish.publish(ROOT, second_build.directory, release_dir=self.release)
        self.assertEqual(second.previous_target, "releases/0.1.0-pub1")
        self.outcome = original

        rolled = publish.rollback(self.release)
        self.assertTrue(rolled.rolled_back)
        self.assertEqual(
            (self.release / "current").resolve(),
            (self.release / "releases" / "0.1.0-pub1").resolve(),
        )
        metadata = json.loads((self.release / "published" / "RELEASE.json").read_text())
        self.assertTrue(metadata["rolled_back"])
        self.assertEqual(metadata["release_path"], "releases/0.1.0-pub1")

    def test_rollback_without_previous_is_refused(self):
        with self.assertRaises(publish.PublishError) as caught:
            publish.rollback(self.release)
        self.assertIn("no previous release", str(caught.exception))

    def test_failed_publish_leaves_live_release_untouched(self):
        self._write_report()
        publish.publish(ROOT, self.outcome.directory, release_dir=self.release)
        before = (self.release / "current").resolve()
        manifest_before = (self.release / "published" / "MANIFEST.json").read_bytes()

        second = self.build("pub3")
        self.outcome = second
        self._write_report()

        def partial_copy_then_fail(source, target, **kwargs):
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / "half-copied.whl").write_bytes(b"partial")
            raise OSError("disk full")

        with mock.patch.object(publish.shutil, "copytree", side_effect=partial_copy_then_fail):
            with self.assertRaises(publish.PublishError):
                publish.publish(ROOT, second.directory, release_dir=self.release)

        self.assertEqual((self.release / "current").resolve(), before)
        self.assertEqual(
            (self.release / "published" / "MANIFEST.json").read_bytes(), manifest_before
        )
        self.assertFalse((self.release / "releases" / "0.1.0-pub3-staging").exists())
        self.assertFalse((self.release / "releases" / "0.1.0-pub3").exists())
        log = (self.release / "logs" / "publish-failed.log").read_text()
        self.assertIn("disk full", log)
        self.assertIn("cleanup=staged release removed", log)

    def test_stale_artifact_blocks_publish(self):
        (self.outcome.directory / "assessment_score_equating-0.0.9-py3-none-any.whl").write_bytes(b"old")
        self._write_report()
        with self.assertRaises(publish.PublishError) as caught:
            publish.publish(ROOT, self.outcome.directory, release_dir=self.release)
        self.assertIn("stale", str(caught.exception))
        self.assertFalse((self.release / "current").exists())


class TestVerificationApproval(ReleaseToolingCase):
    def test_reverification_withdraws_previous_approval(self):
        from releasekit import verify as verify_mod

        candidate = self.tmp / "cand"
        candidate.mkdir()
        report = candidate / "VERIFICATION.json"
        report.write_text(
            json.dumps({"passed": True, "complete": True, "manifest_sha256": "abc"})
        )
        previous = verify_mod.Verifier._invalidate_candidate_report(None, candidate)
        self.assertEqual(previous, "abc")
        data = json.loads(report.read_text())
        self.assertFalse(data["passed"])
        self.assertFalse(data["complete"])
        self.assertIn("withdrawn", data["invalidated"])

    def test_aborted_report_cannot_authorise_publish(self):
        from releasekit import verify as verify_mod

        candidate = self.tmp / "cand"
        candidate.mkdir()
        report = verify_mod.VerificationReport(
            project="assessment-score-equating", version="0.1.0", build_id="x"
        )
        report.complete = True
        # No steps, no reproducibility, no clusters: still not a pass.
        self.assertFalse(report.passed)
        report.steps.append(verify_mod.StepResult("s", "-", "fail", "boom"))
        self.assertFalse(report.passed)


class TestExtraction(ReleaseToolingCase):
    def test_zip_traversal_is_rejected(self):
        import io
        import zipfile as zf_module

        buffer = io.BytesIO()
        with zf_module.ZipFile(buffer, "w") as zf:
            zf.writestr("../escape.txt", "x")
        with self.assertRaises(ValueError):
            fsutil.extract_zip_safely(buffer.getvalue(), self.tmp / "out")

    def test_tar_traversal_is_rejected(self):
        import io
        import tarfile

        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
            tf.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(ValueError):
            fsutil.extract_targz_safely(buffer.getvalue(), self.tmp / "out2")


if __name__ == "__main__":
    unittest.main()
