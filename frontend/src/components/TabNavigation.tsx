import { Home, BookOpen, Calendar, FileText, TrendingUp, Timer } from "lucide-react";

interface TabNavigationProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
  daysUntilExam: number;
}

const tabs = [
  { id: "home", label: "Home", icon: Home, isHome: true },
  { id: "lectures", label: "Lecture Notes & Problem Sheets", icon: BookOpen },
  { id: "calendar", label: "Calendar", icon: Calendar },
  { id: "exams", label: "Exams", icon: FileText },
  { id: "progress", label: "My Progress", icon: TrendingUp },
  { id: "countdown", label: "", icon: Timer, isDynamic: true },
];

const TabNavigation = ({ activeTab, onTabChange, daysUntilExam }: TabNavigationProps) => {
  return (
    <nav className="flex items-center gap-1 p-1.5 bg-card rounded-xl border border-border shadow-sm">
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;
        const Icon = tab.icon;
        const label = tab.isDynamic
          ? `You have ${daysUntilExam} days until your next exam`
          : tab.label;

        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`
              flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 whitespace-nowrap
              ${isActive
                ? "bg-primary text-primary-foreground shadow-md"
                : "text-muted-foreground hover:bg-tab-hover hover:text-foreground"
              }
            `}
          >
            <Icon className="w-4 h-4 shrink-0" />
            <span className="hidden lg:inline">{label}</span>
            <span className="lg:hidden">
              {tab.isDynamic ? `${daysUntilExam}d` : tab.label.split(" ")[0]}
            </span>
          </button>
        );
      })}
    </nav>
  );
};

export default TabNavigation;
