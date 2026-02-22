import { useState } from "react";
import { Search, Sparkles } from "lucide-react";

const HomeScreen = () => {
  const [query, setQuery] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    console.log("Search:", query);
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 py-16">
      <div className="text-center mb-10">
        <h1
          className="text-4xl md:text-5xl font-bold text-foreground mb-3"
          style={{ fontFamily: "Space Grotesk, sans-serif" }}
        >
          What do you want to study?
        </h1>
        <p className="text-muted-foreground text-lg">
          Ask me anything from your lectures, problem sheets or past papers.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="w-full max-w-2xl">
        <div className="relative">
          <div className="absolute left-4 top-1/2 -translate-y-1/2 flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-primary" />
          </div>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask StudyHub AI..."
            className="w-full pl-12 pr-14 py-4 rounded-2xl border border-border bg-card text-foreground text-base placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 shadow-lg"
          />
          <button
            type="submit"
            className="absolute right-3 top-1/2 -translate-y-1/2 p-2 rounded-xl bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
          >
            <Search className="w-5 h-5" />
          </button>
        </div>
      </form>
    </div>
  );
};

export default HomeScreen;
