"""AZ+ bounded FAST->MEDIUM cycle diagnostic with conditional recovery probes."""
from __future__ import annotations
import argparse, csv, time
from pathlib import Path
import serial
from nxw436_driver import NXW436, is_valid_raw_position, signed_delta
from nxw436_progress_detector import evaluate_no_progress

FAST=bytes.fromhex("00E5E3"); MEDIUM=bytes.fromhex("0072F1")
SAMPLE_S=.15; FAST_S=2.; MEDIUM_S=8.; WINDOW_S=1.5; MIN_PROGRESS=20
RESEND_S=2.; KICK_S=.5; AFTER_KICK_S=2.; MAX_BAD=2; MAX_CPS=10_000
class Abort(RuntimeError): pass

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument("--port",default="COM5");p.add_argument("--az-off-tripod",action="store_true")
 p.add_argument("--cycles",type=int,default=3);p.add_argument("--mode",choices=("passive","resend-medium"),default="passive");p.add_argument("--label",required=True);p.add_argument("--observation",default="")
 p.add_argument("--continue-after-stall",action="store_true",help="default stops the series after first confirmed stall/probes")
 p.add_argument("--dry-run",action="store_true")
 a=p.parse_args()
 if not a.az_off_tripod:p.error("AZ movement blocked: pass --az-off-tripod only after securing free non-wrapping travel")
 if not 1<=a.cycles<=20:p.error("--cycles must be 1..20")
 if not a.label.replace("-","").replace("_","").isalnum():p.error("invalid --label")
 print(f"AZ+ cycles={a.cycles}; FAST={FAST.hex().upper()} {FAST_S}s; MEDIUM={MEDIUM.hex().upper()} {MEDIUM_S}s; no-progress={MIN_PROGRESS} counts/{WINDOW_S}s")
 if a.dry_run:return
 rawp=Path(f"nxw436_az_transition_cycles_{a.label}_raw.csv"); evp=Path(f"nxw436_az_transition_cycles_{a.label}_events.csv"); sump=Path(f"nxw436_az_transition_cycles_{a.label}_summary.csv")
 if any(x.exists() for x in(rawp,evp,sump)):p.error("label already exists")
 raw=[];ev=[];summ=[];series_start=time.perf_counter(); commanded=False; c=0; cycle_summary_recorded=False
 def event(c,name,cmd=b"",note=""):ev.append({"cycle_index":c,"monotonic_s":time.perf_counter(),"elapsed_s":time.perf_counter()-series_start,"event":name,"command_hex":cmd.hex().upper(),"note":note})
 def cmd(axis,payload,c,name,mount):
  prefix=0x06; event(c,name,bytes([prefix])+payload);mount.move(axis,"+",payload)
 def stop(c,mount,pre=False):
  cmd("az",b"\0\0\0",c,"PRE_STOP_1" if pre else "STOP_1",mount);time.sleep(.05);cmd("az",b"\0\0\0",c,"PRE_STOP_2" if pre else "STOP_2",mount)
 def read(mount):
  f=mount.query_position_raw("az")
  if f is None:return f,None,"timeout"
  x=int.from_bytes(f,"big");return f,x,"valid" if is_valid_raw_position(x) else "malformed-outside-modulus"
 with NXW436(a.port) as mount:
  try:
   for axis in("az","alt"):
    f=mount.query_position_raw(axis)
    if f is None or not is_valid_raw_position(int.from_bytes(f,"big")):raise Abort(f"preflight {axis.upper()} invalid")
   input("Confirm AZ clearance/stable GND. Observe physical motor if NO_PROGRESS prints; press Enter... ")
   for c in range(1,a.cycles+1):
    cycle_summary_recorded=False; start=""; prev=""; fast_progress=0; medium_progress=0; no=False;resend=False;resend_ok=False;invalid=jumps=timeouts=0
    event(c,"CYCLE_START"); stop(c,mount,pre=True);time.sleep(.25)
    f,start,kind=read(mount)
    if kind!="valid":raise Abort(f"cycle {c} initial {kind}")
    prev=start; prev_t=time.perf_counter(); unwrapped=start; bad=invalid=jumps=timeouts=0;cycle_t=time.perf_counter(); fast_progress=0; medium_progress=0; no=False;resend=False;resend_ok=False;kick=False;kick_ok=False;afterkick_ok=False
    def sample(phase,payload):
     nonlocal prev,prev_t,unwrapped,bad,invalid,jumps,timeouts
     now=time.perf_counter();f,x,k=read(mount);d="";v=False;vel=""
     if k=="valid":
      d=signed_delta(prev,x);lim=max(1000,MAX_CPS*max(now-prev_t,.001))
      if abs(d)>lim:k="implausible-jump";jumps+=1
      else:v=True;bad=0;vel=d/max(now-prev_t,.001);unwrapped+=d;prev=x;prev_t=now
     if not v:
      bad+=1;invalid+=1;timeouts+=k=="timeout"
     raw.append({"cycle_index":c,"monotonic_s":now,"elapsed_s":now-series_start,"cycle_elapsed_s":now-cycle_t,"phase":phase,"direction":"+","payload_hex":payload.hex().upper(),"rx_bytes_hex":"" if f is None else f.hex().upper(),"raw_position":"" if x is None else x,"valid":v,"invalid_reason":"" if v else k,"unwrapped_position":unwrapped,"delta_counts":d,"cumulative_progress":unwrapped-start,"instant_cps":vel})
     if not v and bad>MAX_BAD:raise Abort(f"cycle {c} three consecutive invalid replies")
     return v,now
    def observe(phase,payload,seconds,detect=False):
     nonlocal no
     end=time.perf_counter()+seconds;pts=[]
     while time.perf_counter()<end:
      time.sleep(SAMPLE_S)
      valid,sample_t=sample(phase,payload)
      if valid:
       pts.append((sample_t,unwrapped))
       evidence=evaluate_no_progress(pts, window_s=WINDOW_S,
                                     min_progress_counts=MIN_PROGRESS,
                                     nominal_sample_interval=SAMPLE_S) if detect else None
       if evidence is not None and evidence.confirmed:
        no=True;event(c,"NO_PROGRESS_CONFIRMED",note=f"valid progress {evidence.commanded_progress} counts over {evidence.span_s:.3f}s");print(">>> NO ENCODER PROGRESS - OBSERVE MOTOR: stopped / humming / moving / jerking <<<");return
    cmd("az",FAST,c,"TX_FAST",mount);commanded=True;observe("FAST",FAST,FAST_S);fast_progress=unwrapped-start
    if fast_progress<MIN_PROGRESS:raise Abort(f"cycle {c} FAST progress not confirmed")
    event(c,"FAST_PROGRESS_CONFIRMED",note=str(fast_progress));cmd("az",MEDIUM,c,"TX_MEDIUM",mount);print(f">>> CYCLE {c}: MEDIUM 0072F1 ACTIVE - observe motor physically <<<");medium_start=unwrapped;observe("MEDIUM",MEDIUM,MEDIUM_S,True);medium_progress=unwrapped-medium_start
    outcome="normal-medium-complete"
    if no and a.mode=="resend-medium":
     resend=True;cmd("az",MEDIUM,c,"TX_MEDIUM_RESEND",mount);event(c,"RESEND_RECOVERY_CHECK");before=unwrapped;observe("MEDIUM_RESEND",MEDIUM,RESEND_S);resend_ok=unwrapped-before>=MIN_PROGRESS
     if resend_ok:event(c,"RECOVERED_BY_MEDIUM_RESEND",note=str(unwrapped-before));outcome="recovered-by-medium-resend"
     else:event(c,"RECOVERY_FAILED");outcome="resend-recovery-failed"
    stop(c,mount);commanded=False;event(c,"CYCLE_END",note=outcome)
    summ.append({"cycle_index":c,"direction":"+","start_raw":start,"end_raw":prev,"fast_progress":fast_progress,"medium_progress":medium_progress,"no_progress_detected":no,"resend_attempted":resend,"resend_recovered":resend_ok,"invalid_samples_total":invalid,"implausible_jump_count":jumps,"timeout_count":timeouts,"run_status":"completed","final_outcome":outcome,"operator_observation":a.observation})
    cycle_summary_recorded=True
    if no and not a.continue_after_stall:break
   series_status="completed"
  except (Abort,serial.SerialException,KeyboardInterrupt) as e:
   series_status="aborted";event(c,"SERIES_ABORT",note=type(e).__name__+": "+str(e));print("ABORT:",type(e).__name__,e)
   if c and not cycle_summary_recorded:
    summ.append({"cycle_index":c,"direction":"+","start_raw":start,"end_raw":prev,"fast_progress":fast_progress,"medium_progress":medium_progress,"no_progress_detected":no,"resend_attempted":resend,"resend_recovered":resend_ok,"invalid_samples_total":invalid,"implausible_jump_count":jumps,"timeout_count":timeouts,"run_status":"aborted","final_outcome":type(e).__name__.lower(),"operator_observation":a.observation})
  finally:
   if commanded:
    try:stop(c,mount)
    except Exception as e:event(c,"STOP_ERROR",note=str(e))
 for path,data in((rawp,raw),(evp,ev),(sump,summ)):
  fields=sorted({k for r in data for k in r}) or ["empty"]
  with path.open("w",newline="",encoding="utf-8") as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(data)
 print("Saved:",rawp,evp,sump)
 if series_status!="completed":raise SystemExit(2)
if __name__=="__main__":main()
