import { useState, useEffect, useCallback } from "react";
import { Calendar } from "@/components/ui/calendar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Plus, X, Clock, Wand2, Loader2, RefreshCw, CheckCircle2, CalendarClock, XCircle } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BackendBlock {
  id: string;
  title: string;
  block_type: string;
  start: string;
  end: string;
  duration_minutes: number;
  notes: string | null;
  attended: boolean | null;
  status?: string;   // 'scheduled' | 'completed' | 'missed'
  source?: string;   // 'fixed' | 'generated'
}

interface Event {
  id: string;
  date: string;
  title: string;
  startTime?: string;
  endTime?: string;
  blockType?: string;
  isBackend?: boolean;
  status?: string;
  source?: string;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const USER_ID = "demo-student-001";
const API_BASE = "/api/schedule";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function backendBlockToEvent(block: BackendBlock): Event {
  const startDt = new Date(block.start);
  const endDt = new Date(block.end);
  return {
    id: block.id,
    date: startDt.toISOString().split("T")[0],
    title: block.title,
    startTime: startDt.toTimeString().slice(0, 5),
    endTime: endDt.toTimeString().slice(0, 5),
    blockType: block.block_type,
    isBackend: true,
    status: block.status ?? "scheduled",
    source: block.source ?? "fixed",
  };
}

function blockTypeBadge(blockType?: string) {
  const map: Record<string, { label: string; cls: string }> = {
    lecture:       { label: "Lecture",  cls: "bg-blue-500/20 text-blue-400" },
    seminar:       { label: "Seminar", cls: "bg-violet-500/20 text-violet-400" },
    lab:           { label: "Lab",     cls: "bg-cyan-500/20 text-cyan-400" },
    society:       { label: "Society", cls: "bg-pink-500/20 text-pink-400" },
    sleep:         { label: "Sleep",   cls: "bg-gray-500/20 text-gray-400" },
    sport:         { label: "Sport",   cls: "bg-green-500/20 text-green-400" },
    work:          { label: "Work",    cls: "bg-amber-500/20 text-amber-400" },
    personal:      { label: "Personal",cls: "bg-orange-500/20 text-orange-400" },
    study_session: { label: "Study",   cls: "bg-emerald-500/20 text-emerald-400" },
    other:         { label: "Other",   cls: "bg-gray-500/20 text-gray-400" },
  };
  if (!blockType) return null;
  const entry = map[blockType] ?? map.other;
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${entry.cls}`}>
      {entry.label}
    </span>
  );
}

function statusBadge(status?: string) {
  const map: Record<string, { label: string; cls: string }> = {
    scheduled: { label: "Scheduled", cls: "bg-sky-500/20 text-sky-400" },
    completed: { label: "Completed", cls: "bg-green-500/20 text-green-400" },
    missed:    { label: "Missed",    cls: "bg-red-500/20 text-red-400" },
  };
  if (!status) return null;
  const entry = map[status] ?? map.scheduled;
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${entry.cls}`}>
      {entry.label}
    </span>
  );
}

function statusRowClass(status?: string) {
  if (status === "completed") return "border-l-2 border-l-green-500/60";
  if (status === "missed") return "border-l-2 border-l-red-500/60 opacity-60";
  return "";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const CalendarTab = () => {
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(new Date());
  const [events, setEvents] = useState<Event[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [newStartTime, setNewStartTime] = useState("");
  const [newEndTime, setNewEndTime] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [loading, setLoading] = useState(false);
  const { toast } = useToast();

  // ---- Fetch all events from the backend on mount -------------------

  const fetchSchedule = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/${USER_ID}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blocks: BackendBlock[] = await res.json();
      setEvents(blocks.map(backendBlockToEvent));
    } catch (err) {
      console.error("Failed to fetch schedule:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSchedule();
  }, [fetchSchedule]);

  // ---- Add event — save to backend then refresh ---------------------

  const dateKey = selectedDate?.toISOString().split("T")[0] || "";

  const addEvent = async () => {
    if (!newTitle.trim() || !dateKey) return;

    const startStr = newStartTime || "09:00";
    const endStr = newEndTime || (() => {
      const [h, m] = startStr.split(":").map(Number);
      const endH = Math.min(h + 1, 23);
      return `${String(endH).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
    })();

    const startISO = `${dateKey}T${startStr}:00`;
    const endISO = `${dateKey}T${endStr}:00`;

    const block = {
      title: newTitle.trim(),
      block_type: "other",
      start: new Date(startISO).toISOString(),
      end: new Date(endISO).toISOString(),
    };

    // Optimistic local insert
    const tempId = crypto.randomUUID();
    setEvents((prev) => [
      ...prev,
      { id: tempId, date: dateKey, title: newTitle.trim(), startTime: startStr, endTime: endStr, blockType: "other", isBackend: true },
    ]);
    setNewTitle("");
    setNewStartTime("");
    setNewEndTime("");

    try {
      const res = await fetch(`${API_BASE}/${USER_ID}/blocks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ blocks: [block] }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Replace temp id with the real backend id
      if (data.blocks?.[0]?.id) {
        setEvents((prev) =>
          prev.map((e) => (e.id === tempId ? { ...e, id: data.blocks[0].id } : e))
        );
      }
    } catch (err) {
      console.error("Failed to save block:", err);
      // Roll back optimistic insert
      setEvents((prev) => prev.filter((e) => e.id !== tempId));
      toast({ title: "Save failed", description: "Could not save event to the backend.", variant: "destructive" });
    }
  };

  // ---- Remove event — delete from backend ---------------------------

  const removeEvent = async (id: string) => {
    setEvents((prev) => prev.filter((e) => e.id !== id));
    try {
      await fetch(`${API_BASE}/${USER_ID}/blocks/${id}`, { method: "DELETE" });
    } catch (err) {
      console.error("Failed to delete block:", err);
      fetchSchedule(); // re-sync on failure
    }
  };

  // ---- Auto-generate timetable via backend scheduler ----------------

  const generateTimetable = async () => {
    setIsGenerating(true);
    try {
      const res = await fetch(`${API_BASE}/${USER_ID}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          assignments: [],        // user can populate these later
          config: { strategy: "spread_out" },
        }),
      });
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      const data = await res.json();

      // Refresh from DB to pick up the newly generated study blocks
      await fetchSchedule();

      const count = data.summary?.new_study_blocks ?? 0;
      toast({ title: "Timetable generated", description: `${count} study sessions added to your calendar.` });
    } catch (err: any) {
      toast({ title: "Generation failed", description: err.message || "Could not reach the scheduling API.", variant: "destructive" });
    } finally {
      setIsGenerating(false);
    }
  };

  // ---- Update block status (scheduled / completed / missed) ---------

  const updateBlockStatus = async (blockId: string, status: string) => {
    // Optimistic update
    setEvents((prev) =>
      prev.map((e) => (e.id === blockId ? { ...e, status } : e))
    );
    try {
      const res = await fetch(`${API_BASE}/${USER_ID}/blocks/${blockId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast({ title: "Status updated", description: `Session marked as ${status}.` });
    } catch (err) {
      console.error("Failed to update status:", err);
      await fetchSchedule(); // rollback
      toast({ title: "Update failed", description: "Could not update session status.", variant: "destructive" });
    }
  };

  // ---- Regenerate timetable (re-runs scheduler, keeps completed) ----

  const [isRegenerating, setIsRegenerating] = useState(false);

  const regenerateTimetable = async () => {
    setIsRegenerating(true);
    try {
      const res = await fetch(`${API_BASE}/${USER_ID}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          assignments: [],
          config: { strategy: "spread_out" },
        }),
      });
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      const data = await res.json();
      await fetchSchedule();

      const count = data.summary?.new_study_blocks ?? 0;
      toast({ title: "Timetable regenerated", description: `${count} study sessions regenerated.` });
    } catch (err: any) {
      toast({ title: "Regeneration failed", description: err.message || "Could not reach the scheduling API.", variant: "destructive" });
    } finally {
      setIsRegenerating(false);
    }
  };

  // Build next 7 days from today
  const today = new Date();
  const next7Days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today);
    d.setDate(today.getDate() + i);
    return d.toISOString().split("T")[0];
  });

  const upcomingEvents = events
    .filter((e) => next7Days.includes(e.date))
    .sort((a, b) => a.date.localeCompare(b.date) || (a.startTime || "").localeCompare(b.startTime || ""));

  const eventDates = new Set(events.map((e) => e.date));

  const formatDay = (dateStr: string) => {
    const d = new Date(dateStr + "T12:00:00");
    const todayStr = today.toISOString().split("T")[0];
    const tomorrow = new Date(today);
    tomorrow.setDate(today.getDate() + 1);
    const tomorrowStr = tomorrow.toISOString().split("T")[0];
    if (dateStr === todayStr) return "Today";
    if (dateStr === tomorrowStr) return "Tomorrow";
    return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  };

  // Group upcoming events by date
  const grouped = next7Days.reduce<Record<string, Event[]>>((acc, day) => {
    const dayEvs = upcomingEvents.filter((e) => e.date === day);
    if (dayEvs.length > 0) acc[day] = dayEvs;
    return acc;
  }, {});

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-display font-bold text-foreground mb-1">Calendar</h2>
        <p className="text-muted-foreground">Schedule and manage your study sessions.</p>
      </div>

      <div className="grid md:grid-cols-[auto_1fr] gap-6">
        <div className="space-y-4">
          <div className="bg-card border border-border rounded-xl p-4">
            <Calendar
              mode="single"
              selected={selectedDate}
              onSelect={setSelectedDate}
              modifiers={{ hasEvent: (date) => eventDates.has(date.toISOString().split("T")[0]) }}
              modifiersClassNames={{ hasEvent: "bg-primary/20 font-bold text-primary" }}
              className="pointer-events-auto"
            />
          </div>

          {/* Add event for selected date */}
          <div className="bg-card border border-border rounded-xl p-4 space-y-3">
            <h3 className="text-sm font-semibold text-foreground">
              Add to {selectedDate?.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}
            </h3>
            <Input placeholder="Event title" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addEvent()} className="text-sm" />
            <div className="flex gap-2 items-center">
              <div className="flex items-center gap-1.5 flex-1">
                <span className="text-xs text-muted-foreground whitespace-nowrap">Start</span>
                <Input type="time" value={newStartTime} onChange={(e) => setNewStartTime(e.target.value)} className="text-sm" />
              </div>
              <span className="text-muted-foreground">–</span>
              <div className="flex items-center gap-1.5 flex-1">
                <span className="text-xs text-muted-foreground whitespace-nowrap">End</span>
                <Input type="time" value={newEndTime} onChange={(e) => setNewEndTime(e.target.value)} className="text-sm" />
              </div>
              <Button size="icon" onClick={addEvent} disabled={!newTitle.trim()}>
                <Plus className="w-4 h-4" />
              </Button>
            </div>
          </div>

          {/* Auto-generate timetable */}
          <Button
            onClick={generateTimetable}
            disabled={isGenerating}
            className="w-full gap-2"
            variant="outline"
          >
            {isGenerating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
            {isGenerating ? "Generating…" : "Auto-Generate Timetable"}
          </Button>
          {/* Regenerate timetable */}
          <Button
            onClick={regenerateTimetable}
            disabled={isRegenerating}
            className="w-full gap-2"
            variant="outline"
          >
            {isRegenerating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {isRegenerating ? "Regenerating\u2026" : "Regenerate Timetable"}
          </Button>        </div>

        {/* Upcoming week view */}
        <div className="bg-card border border-border rounded-xl p-5 space-y-4">
          <h3 className="font-display font-semibold text-foreground">Next 7 Days</h3>

          {Object.keys(grouped).length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">No events in the next 7 days</p>
          ) : (
            <div className="space-y-4">
              {next7Days.map((day) => {
                const dayEvs = grouped[day];
                if (!dayEvs) return null;
                return (
                  <div key={day}>
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">{formatDay(day)}</p>
                    <div className="space-y-2">
                      {dayEvs.map((ev) => {
                        const isStudy = ev.blockType === "study_session";
                        return (
                        <Popover key={ev.id}>
                          <PopoverTrigger asChild disabled={!isStudy}>
                            <div className={`flex items-center gap-3 px-4 py-3 bg-muted/50 rounded-lg group ${isStudy ? "cursor-pointer hover:bg-muted/80 transition-colors" : ""} ${statusRowClass(ev.status)}`}>
                              {(ev.startTime || ev.endTime) && (
                                <span className="flex items-center gap-1 text-xs text-muted-foreground shrink-0">
                                  <Clock className="w-3 h-3" />
                                  {ev.startTime || "?"}{ev.endTime ? ` \u2013 ${ev.endTime}` : ""}
                                </span>
                              )}
                              {blockTypeBadge(ev.blockType)}
                              {isStudy && statusBadge(ev.status)}
                              <span className="text-sm text-foreground flex-1">{ev.title}</span>
                              <button onClick={(e) => { e.stopPropagation(); removeEvent(ev.id); }} className="opacity-0 group-hover:opacity-100 transition-opacity">
                                <X className="w-4 h-4 text-destructive" />
                              </button>
                            </div>
                          </PopoverTrigger>
                          {isStudy && (
                            <PopoverContent className="w-48 p-2" side="right" align="start">
                              <p className="text-xs font-semibold text-muted-foreground mb-2 px-1">Mark session as:</p>
                              <div className="flex flex-col gap-1">
                                <button
                                  onClick={() => updateBlockStatus(ev.id, "completed")}
                                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-green-500/10 transition-colors ${ev.status === "completed" ? "bg-green-500/15 text-green-400 font-medium" : "text-foreground"}`}
                                >
                                  <CheckCircle2 className="w-4 h-4 text-green-500" /> Completed
                                </button>
                                <button
                                  onClick={() => updateBlockStatus(ev.id, "scheduled")}
                                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sky-500/10 transition-colors ${ev.status === "scheduled" ? "bg-sky-500/15 text-sky-400 font-medium" : "text-foreground"}`}
                                >
                                  <CalendarClock className="w-4 h-4 text-sky-500" /> Scheduled
                                </button>
                                <button
                                  onClick={() => updateBlockStatus(ev.id, "missed")}
                                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-red-500/10 transition-colors ${ev.status === "missed" ? "bg-red-500/15 text-red-400 font-medium" : "text-foreground"}`}
                                >
                                  <XCircle className="w-4 h-4 text-red-500" /> Missed
                                </button>
                              </div>
                            </PopoverContent>
                          )}
                        </Popover>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default CalendarTab;
