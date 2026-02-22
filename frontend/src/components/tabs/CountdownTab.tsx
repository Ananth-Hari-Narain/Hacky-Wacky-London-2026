import { useState, useEffect, useCallback } from "react";
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Exam {
  id: number;
  name: string;
  date: string;
}

const CountdownTab = () => {
  const [exams, setExams] = useState<Exam[]>(() => {
    try { return JSON.parse(localStorage.getItem("exams") || "[]"); } catch { return []; }
  });
  const [name, setName] = useState("");
  const [date, setDate] = useState("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  const persist = useCallback((next: Exam[]) => {
    setExams(next);
    localStorage.setItem("exams", JSON.stringify(next));
  }, []);

  const addExam = () => {
    setError("");
    if (!name.trim() || !date) { setError("Enter both a name and a date."); return; }
    if (new Date(date) <= new Date()) { setError("Pick a future date and time."); return; }
    persist([...exams, { id: Date.now(), name: name.trim(), date }]);
    setName(""); setDate("");
  };

  const deleteExam = (id: number) => persist(exams.filter(x => x.id !== id));

  const upcoming = exams
    .filter(x => new Date(x.date) > now)
    .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
  const next = upcoming[0] || null;

  const sorted = [...exams].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());

  const countdown = (() => {
    if (!next) return { days: 0, hours: 0, minutes: 0, seconds: 0 };
    let diff = Math.max(0, new Date(next.date).getTime() - now.getTime());
    const days = Math.floor(diff / 86400000); diff %= 86400000;
    const hours = Math.floor(diff / 3600000); diff %= 3600000;
    const minutes = Math.floor(diff / 60000); diff %= 60000;
    const seconds = Math.floor(diff / 1000);
    return { days, hours, minutes, seconds };
  })();

  return (
    <div className="flex flex-col items-center space-y-6 max-w-lg mx-auto w-full">
      <div className="text-center">
        <h1 className="text-2xl font-display font-bold text-primary">Exam Countdown</h1>
        <p className="text-sm text-muted-foreground mt-1">Add your exams — the timer tracks the next one automatically.</p>
      </div>

      {/* Add Exam Card */}
      <div className="bg-card border border-border rounded-xl p-6 w-full space-y-3">
        <h2 className="text-sm font-display font-semibold text-primary/80">Add an Exam</h2>
        <div className="flex gap-2 flex-wrap">
          <Input
            type="text"
            placeholder="Exam name (e.g. Maths Final)"
            value={name}
            onChange={e => setName(e.target.value)}
            className="flex-1 min-w-[150px]"
          />
          <Input
            type="datetime-local"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="flex-1 min-w-[150px]"
          />
        </div>
        <Button onClick={addExam} className="w-full gap-1 bg-gradient-to-r from-violet-600 to-violet-400 hover:opacity-90 text-white border-0">
          <Plus className="w-4 h-4" /> Add Exam
        </Button>
        {error && <p className="text-destructive text-xs">{error}</p>}
      </div>

      {/* Hero Countdown */}
      <div className="w-full rounded-xl p-7 text-center border border-violet-800/50"
        style={{ background: "linear-gradient(135deg, hsl(243 47% 20%), hsl(271 71% 20%))" }}>
        {!next ? (
          <p className="text-muted-foreground py-4">No upcoming exams added yet.</p>
        ) : (
          <>
            <p className="text-[0.7rem] uppercase tracking-[2px] text-violet-400 mb-1">Next Exam</p>
            <p className="text-xl font-display font-bold text-violet-200 mb-5">{next.name}</p>
            <div className="flex justify-center gap-3 flex-wrap">
              {(["days", "hours", "minutes", "seconds"] as const).map(unit => (
                <div key={unit} className="bg-background/40 border border-violet-800/60 rounded-lg px-4 py-3 min-w-[74px]">
                  <div className="text-3xl font-display font-extrabold text-violet-400 leading-none tabular-nums">
                    {String(countdown[unit]).padStart(2, "0")}
                  </div>
                  <div className="text-[0.6rem] uppercase tracking-[1px] text-muted-foreground mt-1">{unit}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* All Exams List */}
      <div className="bg-card border border-border rounded-xl p-6 w-full">
        <h2 className="text-sm font-display font-semibold text-primary/80 mb-3">All Exams</h2>
        {!exams.length ? (
          <p className="text-muted-foreground text-center text-sm py-3">No exams added yet.</p>
        ) : (
          <div className="divide-y divide-border">
            {sorted.map(ex => {
              const isPast = new Date(ex.date) < now;
              return (
                <div key={ex.id} className="flex items-center justify-between py-2.5 gap-2">
                  <div className="min-w-0">
                    <div className="font-semibold text-sm text-foreground truncate">{ex.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {new Date(ex.date).toLocaleString(undefined, {
                        weekday: "short", month: "short", day: "numeric", year: "numeric",
                        hour: "2-digit", minute: "2-digit",
                      })}
                    </div>
                  </div>
                  <span className={`text-[0.65rem] px-2 py-0.5 rounded-full font-bold whitespace-nowrap border ${
                    isPast
                      ? "bg-red-950/50 text-red-400 border-red-900"
                      : "bg-green-950/50 text-green-400 border-green-900"
                  }`}>
                    {isPast ? "Past" : "Upcoming"}
                  </span>
                  <button
                    onClick={() => deleteExam(ex.id)}
                    className="shrink-0 w-7 h-7 rounded-md border border-red-900 text-red-400 flex items-center justify-center hover:bg-red-900/50 transition-colors"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default CountdownTab;
