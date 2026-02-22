import { useState } from "react";
import TabNavigation from "@/components/TabNavigation";
import LectureNotesTab from "@/components/tabs/LectureNotesTab";
import CalendarTab from "@/components/tabs/CalendarTab";
import ExamsTab from "@/components/tabs/ExamsTab";
import ProgressTab from "@/components/tabs/ProgressTab";
import CountdownTab from "@/components/tabs/CountdownTab";
import HomeScreen from "@/components/HomeScreen";
import { GraduationCap } from "lucide-react";

const Index = () => {
  const [activeTab, setActiveTab] = useState("home");
  const daysUntilExam = 0;

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <GraduationCap className="w-6 h-6 text-primary" />
            <span className="font-display font-bold text-lg text-foreground" style={{ fontFamily: 'Space Grotesk, sans-serif' }}>StudyHub</span>
          </div>
        </div>
        <div className="max-w-7xl mx-auto px-4 pb-3 overflow-x-auto">
          <TabNavigation activeTab={activeTab} onTabChange={setActiveTab} daysUntilExam={daysUntilExam} />
        </div>
      </header>

      {/* Content */}
      <main className="flex-1 flex flex-col">
        {activeTab === "home" && <HomeScreen />}
        {activeTab === "lectures" && (
          <div className="max-w-7xl mx-auto px-4 py-8 w-full"><LectureNotesTab /></div>
        )}
        {activeTab === "calendar" && (
          <div className="max-w-7xl mx-auto px-4 py-8 w-full"><CalendarTab /></div>
        )}
        {activeTab === "exams" && (
          <div className="max-w-7xl mx-auto px-4 py-8 w-full"><ExamsTab /></div>
        )}
        {activeTab === "progress" && (
          <div className="max-w-7xl mx-auto px-4 py-8 w-full"><ProgressTab /></div>
        )}
        {activeTab === "countdown" && (
          <div className="max-w-7xl mx-auto px-4 py-8 w-full">
            <CountdownTab />
          </div>
        )}
      </main>
    </div>
  );
};

export default Index;
