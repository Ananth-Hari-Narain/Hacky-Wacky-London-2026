import { useState, useEffect, useCallback } from "react";
import { Calendar } from "@/components/ui/calendar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Plus, X, Clock, RefreshCw, Loader2 } from "lucide-react";

// ---------------------------------------------------------------------------
// Types matching the backend TimeBlock model
// ---------------------------------------------------------------------------

interface BackendBlock {
  id: string;
  title: string;
  block_type: string;
  start: string;   // ISO datetime
  end: string;      // ISO datetime
  duration_minutes: number;
  notes: string | null;
  attended: boolean | null;
}

interface Event {
  id: string;
  date: string;
  title: string;
  time?: string;
  endTime?: string;
  blockType?: string;
  isBackend?: boolean;   // true = came from backend, false = local-only
}

// ---------------------------------------------------------------------------
// Config – change this to your user id
// ---------------------------------------------------------------------------

const USER_ID = "demo-student-001";
const API_BASE = "/api/schedule";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert a backend block into a lightweight Event for the UI. */
function backendBlockToEvent(block: BackendBlock): Event {
  const startDt = new Date(block.start);
  const endDt = new Date(block.end);
  return {
    id: block.id,
    date: startDt.toISOString().split("T")[0],
    title: block.title,
    time: startDt.toTimeString().slice(0, 5),       // "HH:MM"
    endTime: endDt.toTimeString().slice(0, 5),
    blockType: block.block_type,
    isBackend: true,
  };
}

/** Map block_type values to small coloured badges. */
function blockTypeBadge(blockType?: string) {
  const map: Record<string, { label: string; cls: string }> = {
    lecture:        { label: "Lecture",  cls: "bg-blue-500/20 text-blue-400" },
    seminar:        { label: "Seminar", cls: "bg-violet-500/20 text-violet-400" },
    lab:            { label: "Lab",     cls: "bg-cyan-500/20 text-cyan-400" },
    society:        { label: "Society", cls: "bg-pink-500/20 text-pink-400" },
    sleep:          { label: "Sleep",   cls: "bg-gray-500/20 text-gray-400" },
    sport:          { label: "Sport",   cls: "bg-green-500/20 text-green-400" },
    work:           { label: "Work",    cls: "bg-amber-500/20 text-amber-400" },
    personal:       { label: "Personal",cls: "bg-orange-500/20 text-orange-400" },
    study_session:  { label: "Study",   cls: "bg-emerald-500/20 text-emerald-400" },
    other:          { label: "Other",   cls: "bg-gray-500/20 text-gray-400" },
  };
  if (!blockType) return null;
  const entry = map[blockType] ?? map.other;
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${entry.cls}`}>
      {entry.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const CalendarTab = () => {
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(new Date());
  const [events, setEvents] = useState<Event[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [newTime, setNewTime] = useState("");
  const [loading, setLoading] = useState(false);

  // ---- Fetch schedule from backend ----------------------------------

  const fetchSchedule = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/${USER_ID}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blocks: BackendBlock[] = await res.json();
      const backendEvents = blocks.map(backendBlockToEvent);

      // Merge: keep local-only events, replace backend-sourced ones
      setEvents((prev) => {
        const localOnly = prev.filter((e) => !e.isBackend);
        return [...localOnly, ...backendEvents];
      });
    } catch (err) {
      console.error("Failed to fetch schedule:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSchedule();
  }, [fetchSchedule]);

  // ---- Add event (persists to backend as a fixed block) -------------

  const dateKey = selectedDate?.toISOString().split("T")[0] || "";

  const addEvent = async () => {
    if (!newTitle.trim() || !dateKey) return;

    // Build start / end datetimes
    const timeStr = newTime || "09:00";
    const startISO = `${dateKey}T${timeStr}:00`;
    const startDt = new Date(startISO);
    const endDt = new Date(startDt.getTime() + 60 * 60 * 1000); // +1 hour default

    const block = {
      title: newTitle.trim(),
      block_type: "other",
      start: startDt.toISOString(),
      end: endDt.toISOString(),
    };

    // Optimistic local insert
    const tempId = crypto.randomUUID();
    const localEvent: Event = {
      id: tempId,
      date: dateKey,
      title: newTitle.trim(),
      time: timeStr,
      endTime: endDt.toTimeString().slice(0, 5),
      blockType: "other",
      isBackend: true,
    };
    setEvents((prev) => [...prev, localEvent]);
    setNewTitle("");
    setNewTime("");

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
      // Remove the optimistic insert on failure
      setEvents((prev) => prev.filter((e) => e.id !== tempId));
    }
  };

  // ---- Remove event -------------------------------------------------

  const removeEvent = async (id: string, isBackend?: boolean) => {
    setEvents((prev) => prev.filter((e) => e.id !== id));

    if (isBackend) {
      try {
        await fetch(`${API_BASE}/${USER_ID}/blocks/${id}`, { method: "DELETE" });
      } catch (err) {
        console.error("Failed to delete block:", err);
        // Re-fetch to restore state
        fetchSchedule();
      }
    }
  };

  // ---- Derived data for the view ------------------------------------

  const today = new Date();
  const next7Days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today);
    d.setDate(today.getDate() + i);
    return d.toISOString().split("T")[0];
  });

  const upcomingEvents = events
    .filter((e) => next7Days.includes(e.date))
    .sort((a, b) => a.date.localeCompare(b.date) || (a.time || "").localeCompare(b.time || ""));

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

  const grouped = next7Days.reduce<Record<string, Event[]>>((acc, day) => {
    const dayEvs = upcomingEvents.filter((e) => e.date === day);
    if (dayEvs.length > 0) acc[day] = dayEvs;
    return acc;
  }, {});

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-2xl font-display font-bold text-foreground mb-1">Calendar</h2>
            <p className="text-muted-foreground">Schedule and manage your study sessions.</p>
          </div>
          <Button variant="outline" size="sm" onClick={fetchSchedule} disabled={loading} className="gap-1.5">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </Button>
        </div>
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
            <div className="flex gap-2">
              <Input placeholder="Event title" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addEvent()} className="text-sm" />
              <Input type="time" value={newTime} onChange={(e) => setNewTime(e.target.value)} className="w-28 text-sm" />
              <Button size="icon" onClick={addEvent} disabled={!newTitle.trim()}>
                <Plus className="w-4 h-4" />
              </Button>
            </div>
          </div>
        </div>

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
                      {dayEvs.map((ev) => (
                        <div key={ev.id} className="flex items-center gap-3 px-4 py-3 bg-muted/50 rounded-lg group">
                          {ev.time && (
                            <span className="flex items-center gap-1 text-xs text-muted-foreground whitespace-nowrap">
                              <Clock className="w-3 h-3" />
                              {ev.time}{ev.endTime ? `–${ev.endTime}` : ""}
                            </span>
                          )}
                          {blockTypeBadge(ev.blockType)}
                          <span className="text-sm text-foreground flex-1">{ev.title}</span>
                          <button onClick={() => removeEvent(ev.id, ev.isBackend)} className="opacity-0 group-hover:opacity-100 transition-opacity">
                            <X className="w-4 h-4 text-destructive" />
                          </button>
                        </div>
                      ))}
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
