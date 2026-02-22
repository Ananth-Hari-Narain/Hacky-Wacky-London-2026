import { useState } from "react";
import { Calendar } from "@/components/ui/calendar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Plus, X, Clock } from "lucide-react";

interface Event {
  id: string;
  date: string;
  title: string;
  time?: string;
}

const CalendarTab = () => {
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(new Date());
  const [events, setEvents] = useState<Event[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [newTime, setNewTime] = useState("");

  const dateKey = selectedDate?.toISOString().split("T")[0] || "";

  const addEvent = () => {
    if (!newTitle.trim() || !dateKey) return;
    setEvents((prev) => [...prev, { id: crypto.randomUUID(), date: dateKey, title: newTitle.trim(), time: newTime || undefined }]);
    setNewTitle("");
    setNewTime("");
  };

  const removeEvent = (id: string) => setEvents((prev) => prev.filter((e) => e.id !== id));

  // Build next 7 days from today
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
                            <span className="flex items-center gap-1 text-xs text-muted-foreground">
                              <Clock className="w-3 h-3" />
                              {ev.time}
                            </span>
                          )}
                          <span className="text-sm text-foreground flex-1">{ev.title}</span>
                          <button onClick={() => removeEvent(ev.id)} className="opacity-0 group-hover:opacity-100 transition-opacity">
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
