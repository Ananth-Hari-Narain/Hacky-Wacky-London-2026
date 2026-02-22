import { useState } from "react";
import { Upload, FileText, Trash2, ClipboardList, BookCheck, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface ExamFile {
  id: string;
  name: string;
  category: "past-paper" | "markscheme" | "feedback";
  module: string;
  uploadedAt: Date;
}

const categoryConfig = {
  "past-paper": { label: "Past Papers", icon: ClipboardList, color: "text-info" },
  markscheme: { label: "Mark Schemes", icon: BookCheck, color: "text-success" },
  feedback: { label: "Exam Feedback", icon: MessageSquare, color: "text-primary" },
} as const;

const ExamsTab = () => {
  const [files, setFiles] = useState<ExamFile[]>([]);
  const [module, setModule] = useState("");
  const [category, setCategory] = useState<ExamFile["category"]>("past-paper");

  const handleUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const uploaded = e.target.files;
    if (!uploaded || !module.trim()) return;
    const newFiles: ExamFile[] = Array.from(uploaded).map((f) => ({
      id: crypto.randomUUID(),
      name: f.name,
      category,
      module: module.trim(),
      uploadedAt: new Date(),
    }));
    setFiles((prev) => [...prev, ...newFiles]);
    e.target.value = "";
  };

  const removeFile = (id: string) => setFiles((prev) => prev.filter((f) => f.id !== id));

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-display font-bold text-foreground mb-1">Exams</h2>
        <p className="text-muted-foreground">Store past papers, mark schemes, and feedback by module.</p>
      </div>

      <div className="bg-card border border-border rounded-xl p-6 space-y-4">
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="text-sm font-medium text-foreground mb-1 block">Module Name</label>
            <Input placeholder="e.g. Thermodynamics" value={module} onChange={(e) => setModule(e.target.value)} />
          </div>
          <div className="flex gap-2">
            {(Object.keys(categoryConfig) as ExamFile["category"][]).map((cat) => (
              <Button key={cat} variant={category === cat ? "default" : "outline"} size="sm" onClick={() => setCategory(cat)}>
                {categoryConfig[cat].label}
              </Button>
            ))}
          </div>
        </div>

        <label className="flex flex-col items-center justify-center border-2 border-dashed border-border rounded-lg p-8 cursor-pointer hover:border-primary/50 hover:bg-muted/50 transition-colors">
          <Upload className="w-8 h-8 text-muted-foreground mb-2" />
          <span className="text-sm font-medium text-foreground">Click to upload files</span>
          <span className="text-xs text-muted-foreground mt-1">PDF, DOCX, or images</span>
          <input type="file" className="hidden" multiple onChange={handleUpload} disabled={!module.trim()} />
        </label>
      </div>

      <div className="grid md:grid-cols-3 gap-6">
        {(Object.keys(categoryConfig) as ExamFile["category"][]).map((cat) => {
          const cfg = categoryConfig[cat];
          const Icon = cfg.icon;
          const catFiles = files.filter((f) => f.category === cat);
          return (
            <div key={cat} className="bg-card border border-border rounded-xl p-5">
              <div className="flex items-center gap-2 mb-4">
                <Icon className={`w-5 h-5 ${cfg.color}`} />
                <h3 className="font-display font-semibold text-foreground">{cfg.label}</h3>
                <span className="ml-auto text-xs text-muted-foreground">{catFiles.length}</span>
              </div>
              {catFiles.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-6">None yet</p>
              ) : (
                <div className="space-y-1">
                  {catFiles.map((f) => (
                    <div key={f.id} className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-muted/60 transition-colors group">
                      <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-foreground truncate">{f.name}</p>
                        <p className="text-xs text-muted-foreground">{f.module}</p>
                      </div>
                      <button onClick={() => removeFile(f.id)} className="opacity-0 group-hover:opacity-100 transition-opacity">
                        <Trash2 className="w-4 h-4 text-destructive" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ExamsTab;
