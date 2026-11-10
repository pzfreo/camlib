"""Named Haas post parity: bytes, independent decoding, replay and release gate."""

from dataclasses import asdict, replace
import json
import unittest

from camlib._buffers import native
from camlib.post import LoweringOptions, post_job, post_job_input
from test_rust_post import ROOT, example, plain, python_release


def machine_for(job, **settings):
    from test_drill_cycles import DrillCycleTest

    machine = DrillCycleTest.haas(None, job, **settings)[1]
    if settings.get("cutter_compensation"):
        machine = replace(machine, tools=tuple(replace(t, diameter_register=7) for t in machine.tools))
    return machine


class RustHaasPostTest(unittest.TestCase):
    def assert_job(self, job, machine, accept=("unchecked_machine",), lowering=None):
        haas = example("haas_ngc_post")
        python = python_release(
            job,
            machine,
            set(accept),
            post=haas.HaasNGCPost,
            decoder=haas.HaasNGCDecoder,
            lowering=lowering,
        )
        source = post_job_input(job, "top", machine=machine, accept=accept, lowering=lowering)
        rust = post_job(source)
        self.assertEqual(rust["released"], python["released"])
        self.assertEqual(rust["findings"], plain(python["findings"]))
        if python["released"]:
            self.assertEqual(rust["program"], python["text"])
            for key in ("lines", "motions", "accepted", "unchecked", "machine"):
                self.assertEqual(rust[key], plain(python[key]), key)
        else:
            self.assertIsNone(rust["program"])
            self.assertEqual(rust["refused"]["statuses"], python["statuses"])
        return rust

    def test_jobs_cycles_and_gate(self):
        from test_drill_cycles import job, boring_job
        from test_dwell import job as dwell_job
        from test_tapping import job as tap_job, tapping
        from test_cutter_compensation import job as contour_job

        cases = [
            (job(), {}, "G81 "),
            (job(dwell_s=0.5), {}, "G82 "),
            (job(peck_mm=1.5), dict(setting_22_mm=0.254, setting_52_mm=0), "G83 "),
            (job(peck_mm=1.5, peck_mode="chip_break"), dict(setting_22_mm=0.254), "G73 "),
            (boring_job(), {}, "G85 "),
            (boring_job(dwell_s=0.4), {}, "G89 "),
            (dwell_job(canned_cycle=False), {}, "G4 P0.5"),
            (tap_job(tapping), dict(rigid_tapping=True), "G84 "),
            (contour_job(compensation="wear"), dict(cutter_compensation=True), "G1 G41 "),
            (boring_job(spindle_stop=True), {}, "M5"),
        ]
        for j, options, code in cases:
            with self.subTest(code=code):
                rust = self.assert_job(j, machine_for(j, **options), lowering=LoweringOptions())
                self.assertTrue(rust["released"])
                self.assertIn(code, rust["program"])
                self.assertNotIn("G86", rust["program"])
        j = job()
        self.assertFalse(self.assert_job(j, machine_for(j), accept=())["released"])

    def test_job_golden_and_machine_model(self):
        from test_drill_cycles import job
        from test_machine import model
        from camlib.machine import Box

        j = job(dwell_s=0.5)
        m = machine_for(j)
        rust = self.assert_job(j, m)
        f = ROOT / "fixtures/posts/rust/haas-drill"
        self.assertEqual(rust["program"], f.with_suffix(".nc").read_text())
        source = plain(post_job_input(j, "top", machine=m, accept=("unchecked_machine",)))
        source["manifest"]["run"]["id"] = "haas-drill-fixture"
        self.assertEqual(source, json.loads(f.with_suffix(".json").read_text()))
        header = j.compile().setup("top").program_header()
        declared = model(header, m)
        # This drill has a 6 mm holder radius; the generic test model's
        # 5 mm indexing box does not enclose it. Declare the actual bound.
        t = header.tools[0]
        declared = replace(
            declared,
            tool_change_routes={
                t.id: replace(
                    declared.tool_change_routes[t.id],
                    index_envelopes=(Box((-7, -7, -t.gauge_length_mm - 1), (7, 7, 1)),),
                ),
            },
        )
        p = header.initial_position_mm
        declared = replace(declared, initial_axes_mm=(p[0], p[1], p[2] + header.tools[0].gauge_length_mm))
        rust = self.assert_job(j, replace(m, crash_model=declared), accept=())
        self.assertEqual(rust["machine"]["status"], "clear")
        self.assertEqual(rust["machine"]["decoded_motions"], rust["motions"])

    def test_existing_haas_programs_and_golden(self):
        from test_haas_ngc_post import HaasNGCTest

        haas = example("haas_ngc_post")
        for units in ("mm", "inch"):
            for rotation in (True, False):
                p, m = HaasNGCTest.machine(None, output_units=units, rotation_scaling=rotation)
                python = "".join(c.text for c in haas.post_haas(p, m))
                source = dict(program=p.to_dict(), machine=m.to_dict(), lowering=asdict(LoweringOptions()))
                rust = json.loads(native.post_program(json.dumps(source)))
                self.assertEqual(rust["program"], python)
                if units == "mm" and rotation:
                    fixture = ROOT / "fixtures/posts/rust/haas-program"
                    self.assertEqual(json.loads(fixture.with_suffix(".json").read_text()), plain(source))
                    self.assertEqual(fixture.with_suffix(".nc").read_text(), python)

    def test_existing_haas_arcs(self):
        from test_haas_ngc_post import HaasNGCTest
        from camlib.motion import Arc, Helix, Motion

        haas = example("haas_ngc_post")
        for curve in (
            Arc((5, 0, 3), (0, 5, 3), (0, 0, 3), 1.5707963267948966),
            Arc((5, 0, 3), (5, 0, 3), (0, 0, 3), 6.283185307179586),
            Helix((5, 0, 3), (5, 0, 2), (0, 0, 3), 6.283185307179586),
        ):
            p, m = HaasNGCTest.machine(None, curve_policy="exact_ijk")
            p = replace(p, events=(*p.events[:3], Motion(1, "arc", "t1", curve, 300)))
            python = "".join(c.text for c in haas.post_haas(p, m))
            rust = json.loads(native.post_program(json.dumps(dict(program=p.to_dict(), machine=m.to_dict()))))
            self.assertEqual(rust["program"], python)

    def test_refusals(self):
        from test_drill_cycles import job
        from test_tapping import job as tap_job, tapping

        haas = example("haas_ngc_post")
        for j, settings, message in (
            (job(peck_mm=1.5), {}, "G83 needs Setting 22"),
            (job(peck_mm=1.5), dict(setting_22_mm=0.254), "G83 needs Setting 52"),
            (job(peck_mm=1.5), dict(setting_22_mm=0.254, setting_52_mm=2), "clears chips above R"),
            (job(peck_mm=1.5), dict(setting_22_mm=0.5, setting_52_mm=0), "differs from Setting 22"),
            (job(peck_mm=1.5, peck_mode="chip_break"), {}, "G73 needs Setting 22"),
            (tap_job(tapping), {}, "declares no tapping cycle"),
        ):
            m = machine_for(j, **settings)
            with self.subTest(message=message):
                with self.assertRaisesRegex(Exception, message):
                    j.post_nc("top", machine=m, post=haas.HaasNGCPost, decoder=haas.HaasNGCDecoder)
                with self.assertRaisesRegex(ValueError, message):
                    post_job(post_job_input(j, "top", machine=m))


if __name__ == "__main__":
    unittest.main()
