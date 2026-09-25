"""Bounded AZ FAST->MEDIUM transition diagnostic; never alters the goto controller."""
from __future__ import annotations

import argparse, csv, time, subprocess, sys
from pathlib import Path
import serial
from nxw436_driver import NXW436, is_valid_raw_position, signed_delta

FAST = bytes.fromhex("00E5E3")
MEDIUM = bytes.fromhex("0072F1")
SAMPLE_S = 0.15
FAST_SECONDS = 2.0
MEDIUM_SECONDS = 8.0
PROGRESS_WINDOW_S = 1.5
MIN_PROGRESS_COUNTS = 20
POST_NO_PROGRESS_S = 1.0
KICK_SECONDS = 0.5
RECOVERY_SECONDS = 3.0
MAX_BAD = 2
MAX_CPS = 10_000

class Abort(RuntimeError): pass

def valid_frame(mount, axis="az"):
    frame = mount.query_position_raw(axis)
    if frame is None: return None, None, "timeout"
    raw = int.from_bytes(frame, "big")
    return frame, raw, "valid" if is_valid_raw_position(raw) else "malformed-outside-modulus"

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("direction", choices=("+", "-"))
    p.add_argument("--mode", choices=("passive","resend-medium","kick"), default="passive")
    p.add_argument("--port", default="COM5"); p.add_argument("--az-off-tripod", action="store_true")
    p.add_argument("--label", required=True); p.add_argument("--observation", default="")
    p.add_argument("--fast-seconds", type=float, default=FAST_SECONDS); p.add_argument("--medium-seconds", type=float, default=MEDIUM_SECONDS)
    p.add_argument("--cycles", type=int, default=1, help="1..20; multi-cycle resend hunt when greater than one")
    p.add_argument("--dry-run", action="store_true")
    a=p.parse_args()
    if not a.az_off_tripod: p.error("AZ movement blocked: secure free non-wrapping travel, then pass --az-off-tripod")
    if not a.label.replace("-","").replace("_","").isalnum(): p.error("invalid --label")
    if not .5 <= a.fast_seconds <= 4 or not 3 <= a.medium_seconds <= 10: p.error("fast 0.5..4 s; medium 3..10 s")
    if not 1 <= a.cycles <= 20: p.error("--cycles must be 1..20")
    print(f"mode={a.mode}; FAST={FAST.hex().upper()} {a.fast_seconds}s; MEDIUM={MEDIUM.hex().upper()} {a.medium_seconds}s; progress window={PROGRESS_WINDOW_S}s / {MIN_PROGRESS_COUNTS} counts")
    if a.dry_run: return
    if a.cycles > 1:
        if a.mode == "kick": p.error("--mode kick is not available for multi-cycle hunt")
        command=[sys.executable,"nxw436_az_transition_cycles.py","--port",a.port,"--az-off-tripod","--cycles",str(a.cycles),"--mode",a.mode,"--label",a.label]
        if a.observation: command += ["--observation",a.observation]
        raise SystemExit(subprocess.call(command))
    raw_path=Path(f"nxw436_az_transition_{a.label}_raw.csv"); event_path=Path(f"nxw436_az_transition_{a.label}_events.csv"); summary_path=Path(f"nxw436_az_transition_{a.label}_summary.csv")
    if any(x.exists() for x in (raw_path,event_path,summary_path)): p.error("label already exists")
    sign=1 if a.direction=="+" else -1; rows=[]; events=[]; summary={"label":a.label,"direction":a.direction,"mode":a.mode,"fast_seconds":a.fast_seconds,"medium_seconds":a.medium_seconds,"progress_window_s":PROGRESS_WINDOW_S,"min_progress_counts":MIN_PROGRESS_COUNTS,"observation":a.observation,"run_status":"not_started"}
    started=0.; previous_raw=None; previous_time=0.; unwrapped=0; start_raw=0; bad=0; no_progress=False; movement_commanded=False
    def event(kind, payload=b"", note=""):
        events.append({"elapsed_s":time.perf_counter()-started,"event":kind,"command_hex":payload.hex().upper(),"note":note})
    def stop_twice(mount):
        event("TX_STOP_1", bytes([0x06 if a.direction=="+" else 0x07,0,0,0])); mount.move("az",a.direction,b"\0\0\0"); time.sleep(.05)
        event("TX_STOP_2", bytes([0x06 if a.direction=="+" else 0x07,0,0,0])); mount.move("az",a.direction,b"\0\0\0")
    def sample(mount, phase):
        nonlocal previous_raw,previous_time,unwrapped,bad,no_progress
        now=time.perf_counter(); frame,raw,kind=valid_frame(mount); elapsed=now-started; delta=""; instant_cps=""; valid=False
        if kind=="valid":
            delta=signed_delta(previous_raw,raw); limit=max(1000,MAX_CPS*max(now-previous_time,.001))
            if abs(delta)>limit: kind="implausible-jump"
            else:
                valid=True; bad=0; instant_cps=delta/max(now-previous_time,.001); unwrapped+=delta; previous_raw,previous_time=raw,now
        if not valid: bad+=1
        rows.append({"monotonic_s":now,"elapsed_s":elapsed,"phase":phase,"direction":a.direction,"payload_hex":"00E5E3" if phase in ("FAST","KICK") else ("0072F1" if phase.startswith("MEDIUM") else "000000"),"rx_bytes_hex":"" if frame is None else frame.hex().upper(),"raw_position":"" if raw is None else raw,"valid":valid,"invalid_reason":"" if valid else kind,"unwrapped_position":unwrapped,"delta_counts":delta,"instant_cps":instant_cps,"cumulative_from_start":unwrapped-start_raw})
        if bad>MAX_BAD: raise Abort("three consecutive invalid replies")
        return valid
    def observe(mount, phase, seconds, detect=False):
        nonlocal no_progress
        deadline=time.perf_counter()+seconds; points=[]
        while time.perf_counter()<deadline:
            time.sleep(SAMPLE_S); valid=sample(mount,phase)
            if valid: points.append((time.perf_counter()-started,unwrapped))
            if detect:
                recent=[x for x in points if x[0]>=points[-1][0]-PROGRESS_WINDOW_S] if points else []
                if recent and recent[-1][0]-recent[0][0]>=PROGRESS_WINDOW_S and (recent[-1][1]-recent[0][1])*sign<MIN_PROGRESS_COUNTS:
                    no_progress=True; event("NO_PROGRESS",note="valid-replies, rolling commanded-direction progress below threshold")
                    print(">>> NO ENCODER PROGRESS - OBSERVE MOTOR: stopped / humming / moving / jerking <<<")
                    return
    with NXW436(a.port) as mount:
        try:
            for axis in ("az","alt"):
                frame,raw,kind=valid_frame(mount, axis)
                if kind!="valid": raise Abort(f"preflight {axis.upper()} {kind}")
                summary[f"preflight_{axis}_raw"]=raw; summary[f"preflight_{axis}_rx"]=frame.hex().upper()
            print(f"PREFLIGHT AZ={summary['preflight_az_raw']:06X} ALT={summary['preflight_alt_raw']:06X}")
            input("Confirm AZ clearance, stable GND, and observe motor at MEDIUM marker; press Enter... ")
            started=time.perf_counter()
            stop_twice(mount); time.sleep(.25)
            frame,start_raw,kind=valid_frame(mount)
            if kind!="valid": raise Abort(f"initial AZ {kind}")
            previous_raw=start_raw; previous_time=time.perf_counter(); unwrapped=start_raw; movement_commanded=True
            event("TX_FAST", bytes([0x06 if a.direction=="+" else 0x07])+FAST); mount.move("az",a.direction,FAST)
            observe(mount,"FAST",a.fast_seconds)
            if (unwrapped-start_raw)*sign<MIN_PROGRESS_COUNTS: raise Abort("FAST did not establish commanded-direction encoder motion")
            event("TX_MEDIUM", bytes([0x06 if a.direction=="+" else 0x07])+MEDIUM); mount.move("az",a.direction,MEDIUM)
            print(">>> MEDIUM 0072F1 ACTIVE - observe motor physically <<<")
            observe(mount,"MEDIUM",a.medium_seconds,detect=True)
            if no_progress:
                observe(mount,"MEDIUM_POST_NO_PROGRESS",POST_NO_PROGRESS_S)
                if a.mode=="resend-medium":
                    event("TX_RESEND_MEDIUM", bytes([0x06 if a.direction=="+" else 0x07])+MEDIUM); mount.move("az",a.direction,MEDIUM); observe(mount,"MEDIUM_RESEND",RECOVERY_SECONDS)
                elif a.mode=="kick":
                    event("TX_KICK_FAST", bytes([0x06 if a.direction=="+" else 0x07])+FAST); mount.move("az",a.direction,FAST); observe(mount,"KICK",KICK_SECONDS)
                    event("TX_MEDIUM_AFTER_KICK", bytes([0x06 if a.direction=="+" else 0x07])+MEDIUM); mount.move("az",a.direction,MEDIUM); observe(mount,"MEDIUM_AFTER_KICK",RECOVERY_SECONDS)
            summary.update({"run_status":"completed","no_progress_detected":no_progress,"final_raw":previous_raw,"final_unwrapped":unwrapped,"total_elapsed_s":time.perf_counter()-started,"invalid_samples_total":sum(not r['valid'] for r in rows)})
        except (Abort,serial.SerialException,KeyboardInterrupt) as e:
            summary.update({"run_status":"aborted","abort_reason":type(e).__name__+": "+str(e)})
            print("ABORT:",summary["abort_reason"])
        finally:
            if movement_commanded:
                try: stop_twice(mount)
                except Exception as e: summary["stop_error"]=str(e)
    for path,data in ((raw_path,rows),(event_path,events)):
        fields=sorted({k for r in data for k in r}) or ["empty"]
        with path.open("w",newline="",encoding="utf-8") as f: w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(data)
    with summary_path.open("w",newline="",encoding="utf-8") as f: w=csv.DictWriter(f,fieldnames=sorted(summary));w.writeheader();w.writerow(summary)
    print("Saved:",raw_path,event_path,summary_path)
    if summary["run_status"]!="completed": raise SystemExit(2)
if __name__=="__main__": main()
