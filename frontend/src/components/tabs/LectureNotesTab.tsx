import { useState } from "react";
import { Upload, FileText, Trash2, FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface UploadedFile {
  id: string;
  name: string;
  type: "lecture" | "problem-sheet";
  module: string;
  uploadedAt: Date;
}

const LectureNotesTab = () => {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [module, setModule] = useState("");
  const [fileType, setFileType] = useState<"lecture" | "problem-sheet">("lecture");

  const handleUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const uploaded = e.target.files;
    if (!uploaded || !module.trim()) return;

    const newFiles: UploadedFile[] = Array.from(uploaded).map((f) => ({
      id: crypto.randomUUID(),
      name: f.name,
      type: fileType,
      module: module.trim(),
      uploadedAt: new Date(),
    }));
    setFiles((prev) => [...prev, ...newFiles]);
    e.target.value = "";
  };

  const removeFile = (id: string) => setFiles((prev) => prev.filter((f) => f.id !== id));

  const lectures = files.filter((f) => f.type === "lecture");
  const problemSheets = files.filter((f) => f.type === "problem-sheet");

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-display font-bold text-foreground mb-1">Lecture Notes & Problem Sheets</h2>
        <p className="text-muted-foreground">Upload and organise your study materials by module.</p>
      </div>

      {/* Upload area */}
      <div className="bg-card border border-border rounded-xl p-6 space-y-4">
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="text-sm font-medium text-foreground mb-1 block">Module Name</label>
            <Input placeholder="e.g. Linear Algebra" value={module} onChange={(e) => setModule(e.target.value)} />
          </div>
          <div className="flex gap-2">
            <Button
              variant={fileType === "lecture" ? "default" : "outline"}
              size="sm"
              onClick={() => setFileType("lecture")}
            >
              Lecture Note
            </Button>
            <Button
              variant={fileType === "problem-sheet" ? "default" : "outline"}
              size="sm"
              onClick={() => setFileType("problem-sheet")}
            >
              Problem Sheet
            </Button>
          </div>
        </div>

        <label className="flex flex-col items-center justify-center border-2 border-dashed border-border rounded-lg p-8 cursor-pointer hover:border-primary/50 hover:bg-muted/50 transition-colors">
          <Upload className="w-8 h-8 text-muted-foreground mb-2" />
          <span className="text-sm font-medium text-foreground">Click to upload files</span>
          <span className="text-xs text-muted-foreground mt-1">PDF, DOCX, or images</span>
          <input type="file" className="hidden" multiple onChange={handleUpload} disabled={!module.trim()} />
        </label>
      </div>

      {/* File lists */}
      <div className="grid md:grid-cols-2 gap-6">
        <FileList title="Lecture Notes" icon={<FileText className="w-5 h-5 text-info" />} files={lectures} onRemove={removeFile} />
        <FileList title="Problem Sheets" icon={<FolderOpen className="w-5 h-5 text-primary" />} files={problemSheets} onRemove={removeFile} />
      </div>
    </div>
  );
};

const FileList = ({ title, icon, files, onRemove }: { title: string; icon: React.ReactNode; files: UploadedFile[]; onRemove: (id: string) => void }) => {
  const grouped = files.reduce<Record<string, UploadedFile[]>>((acc, f) => {
    (acc[f.module] = acc[f.module] || []).push(f);
    return acc;
  }, {});

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-4">
        {icon}
        <h3 className="font-display font-semibold text-foreground">{title}</h3>
        <span className="ml-auto text-xs text-muted-foreground">{files.length} files</span>
      </div>
      {Object.keys(grouped).length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-6">No files uploaded yet</p>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped).map(([mod, modFiles]) => (
            <div key={mod}>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">{mod}</p>
              <div className="space-y-1">
                {modFiles.map((f) => (
                  <div key={f.id} className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-muted/60 transition-colors group">
                    <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                    <span className="text-sm text-foreground truncate flex-1">{f.name}</span>
                    <button onClick={() => onRemove(f.id)} className="opacity-0 group-hover:opacity-100 transition-opacity">
                      <Trash2 className="w-4 h-4 text-destructive" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default LectureNotesTab;
