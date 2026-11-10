//! Python-authored Carvera bytes, independently decoded and replayed.
use camlib::gcode::{Machine, decode::Decoder};
use serde_json::{Value, json};
use std::{collections::BTreeSet, sync::atomic::AtomicBool};
const DRILL: &str = include_str!("../fixtures/posts/rust/carvera-drill.json");
const ARCS: &str = include_str!("../fixtures/posts/rust/carvera-arcs.json");
fn report(source: &str) -> Value {
    serde_json::from_str(&camlib::post_job::run(source, &AtomicBool::new(false)).unwrap()).unwrap()
}
#[test]
fn python_programs_are_byte_identical_and_checked() {
    for (source, nc) in [
        (
            DRILL,
            include_str!("../fixtures/posts/rust/carvera-drill.nc"),
        ),
        (ARCS, include_str!("../fixtures/posts/rust/carvera-arcs.nc")),
    ] {
        let r = report(source);
        assert_eq!(r["released"], true);
        assert_eq!(r["program"], nc);
        assert_eq!(r["motions"], r["posting"]["checked_motions"]);
        assert_eq!(
            r["posting"]["definition"]["machine"]["controller"]["name"],
            "carvera"
        );
    }
    let r = report(ARCS);
    assert!(r["posting"]["arc_output"]["native"].as_u64().unwrap() > 0);
    for k in ["lines_policy", "lines_machine", "lines_precision"] {
        assert_eq!(r["posting"]["arc_output"][k], 0);
    }
}
#[test]
fn stock_replay_applies_the_export_gate() {
    let mut source: Value = serde_json::from_str(DRILL).unwrap();
    source["accept"] = json!([]);
    let r = report(&source.to_string());
    assert_eq!(r["released"], false);
    assert!(r["program"].is_null());
    assert_eq!(r["refused"]["statuses"], json!(["unchecked_machine"]));
}
fn decoder() -> Decoder {
    let input: Value = serde_json::from_str(DRILL).unwrap();
    let m: Machine = serde_json::from_value(input["machine"].clone()).unwrap();
    let mut d = Decoder::new(&m, [0., 0., 12.], BTreeSet::new()).unwrap();
    for l in ["G21", "G90", "G17", "G94", "G54", "M5", "M9"] {
        d.read_line(l, None).unwrap();
    }
    d.validate_initialized().unwrap();
    d
}
#[test]
fn independent_decoder_rejects_unsupported_words_and_end_state() {
    for line in [
        "G20",
        "G43 H1",
        "G49",
        "G61",
        "G64",
        "G81 X1 Y1 Z0 R2 F100",
        "M8",
        "M4 S1000",
        "M0",
        "M1",
        "M6",
        "T1",
        "H1",
        "t1",
        "h1",
        "m06",
        "M30",
        "G53 Z-3",
        "G53 G0 Z-2",
    ] {
        assert!(decoder().read_line(line, None).is_err(), "{line}");
    }
    let mut d = decoder();
    d.read_line("G53 G0 Z-3", None).unwrap();
    d.read_line("M30", None).unwrap();
    assert!(d.ended);
    assert!(d.read_line("G21", None).is_err());
}
#[test]
fn refuses_inches_unknown_options_and_unplanned_retracts() {
    for (path, value, message) in [
        (
            vec!["machine", "output_units"],
            json!("inch"),
            "requires mm",
        ),
        (
            vec!["machine", "controller", "unknown"],
            json!(true),
            "unknown field",
        ),
        (
            vec!["machine", "controller", "automatic_tool_length"],
            json!(false),
            "automatic M6",
        ),
        (
            vec!["machine", "controller", "initial_gauge_length_mm"],
            json!(0),
            "plan the retract",
        ),
        (vec!["machine", "flood"], json!(true), "no flood"),
    ] {
        let mut source: Value = serde_json::from_str(DRILL).unwrap();
        let mut target = &mut source;
        for key in path {
            target = &mut target[key];
        }
        *target = value;
        let error =
            camlib::post_job::run(&source.to_string(), &AtomicBool::new(false)).unwrap_err();
        assert!(error.contains(message), "{error}");
    }
    for effect in [
        "units_inch",
        "length_on",
        "drill_cycle",
        "exact_stop",
        "spindle_ccw",
        "flood",
    ] {
        let mut source: Value = serde_json::from_str(DRILL).unwrap();
        source["machine"]["dialect"]["commands"]
            .as_array_mut()
            .unwrap()
            .push(json!({"effect":effect,"code":"G999"}));
        assert!(
            camlib::post_job::run(&source.to_string(), &AtomicBool::new(false))
                .unwrap_err()
                .contains("command mapping")
        );
    }
}

#[test]
fn explicit_program_matches_python_and_refuses_an_unplanned_final_retract() {
    let source = include_str!("../fixtures/posts/rust/carvera-program.json");
    let r: Value = serde_json::from_str(
        &camlib::gcode::post_program(source, &AtomicBool::new(false)).unwrap(),
    )
    .unwrap();
    assert_eq!(
        r["program"],
        include_str!("../fixtures/posts/rust/carvera-program.nc")
    );
    let mut input: Value = serde_json::from_str(source).unwrap();
    input["program"]["events"][4]["motion"]["curve"]["end"][2] = json!(11);
    assert!(
        camlib::gcode::post_program(&input.to_string(), &AtomicBool::new(false))
            .unwrap_err()
            .contains("plan the retract")
    );
}
