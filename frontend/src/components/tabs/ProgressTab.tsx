import { useState } from "react";
import { TrendingUp, CheckCircle2, XCircle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

interface ModuleScore {
  module: string;
  correct: number;
  total: number;
}

const sampleQuestions = [
  { id: "1", module: "Linear Algebra", question: "What is the determinant of a 2×2 identity matrix?", answer: "1" },
  { id: "2", module: "Linear Algebra", question: "Is the zero vector linearly independent?", answer: "No" },
  { id: "3", module: "Calculus", question: "What is the derivative of sin(x)?", answer: "cos(x)" },
  { id: "4", module: "Calculus", question: "What is ∫ 1/x dx?", answer: "ln|x| + C" },
  { id: "5", module: "Thermodynamics", question: "What does the first law of thermodynamics state about energy?", answer: "Energy is conserved" },
  { id: "6", module: "Thermodynamics", question: "What is entropy a measure of?", answer: "Disorder" },
];

const ProgressTab = () => {
  const [scores, setScores] = useState<ModuleScore[]>([]);
  const [currentQ, setCurrentQ] = useState(0);
  const [userAnswer, setUserAnswer] = useState("");
  const [showAnswer, setShowAnswer] = useState(false);
  const [quizActive, setQuizActive] = useState(false);

  const startQuiz = () => {
    setQuizActive(true);
    setCurrentQ(0);
    setUserAnswer("");
    setShowAnswer(false);
  };

  const submitAnswer = (correct: boolean) => {
    const q = sampleQuestions[currentQ];
    setScores((prev) => {
      const existing = prev.find((s) => s.module === q.module);
      if (existing) {
        return prev.map((s) =>
          s.module === q.module ? { ...s, correct: s.correct + (correct ? 1 : 0), total: s.total + 1 } : s
        );
      }
      return [...prev, { module: q.module, correct: correct ? 1 : 0, total: 1 }];
    });
    setShowAnswer(false);
    setUserAnswer("");
    if (currentQ < sampleQuestions.length - 1) {
      setCurrentQ((p) => p + 1);
    } else {
      setQuizActive(false);
    }
  };

  const getStrength = (pct: number) => {
    if (pct >= 70) return { label: "Strong", class: "bg-strength-high" };
    if (pct >= 40) return { label: "Developing", class: "bg-strength-medium" };
    return { label: "Needs Work", class: "bg-strength-low" };
  };

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-display font-bold text-foreground mb-1">My Progress</h2>
        <p className="text-muted-foreground">Test yourself and track your strength across modules.</p>
      </div>

      {/* Strength scores */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {scores.length === 0 ? (
          <div className="col-span-full bg-card border border-border rounded-xl p-8 text-center">
            <TrendingUp className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
            <p className="text-muted-foreground">Complete a quiz to see your strength scores</p>
          </div>
        ) : (
          scores.map((s) => {
            const pct = Math.round((s.correct / s.total) * 100);
            const strength = getStrength(pct);
            return (
              <div key={s.module} className="bg-card border border-border rounded-xl p-5 space-y-3">
                <div className="flex items-center justify-between">
                  <h4 className="font-display font-semibold text-foreground">{s.module}</h4>
                  <span className={`text-xs font-bold px-2 py-0.5 rounded-full text-white ${strength.class}`}>
                    {strength.label}
                  </span>
                </div>
                <Progress value={pct} className="h-2" />
                <p className="text-sm text-muted-foreground">
                  {s.correct}/{s.total} correct — {pct}%
                </p>
              </div>
            );
          })
        )}
      </div>

      {/* Quiz section */}
      <div className="bg-card border border-border rounded-xl p-6">
        {!quizActive ? (
          <div className="text-center space-y-4">
            <h3 className="font-display text-lg font-semibold text-foreground">Ready to test yourself?</h3>
            <p className="text-sm text-muted-foreground">Questions are drawn from your modules. Answer honestly to build accurate strength scores.</p>
            <div className="flex gap-3 justify-center">
              <Button onClick={startQuiz}>Start Quiz</Button>
              {scores.length > 0 && (
                <Button variant="outline" onClick={() => setScores([])}>
                  <RotateCcw className="w-4 h-4 mr-1" /> Reset Scores
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
                {sampleQuestions[currentQ].module}
              </span>
              <span className="text-xs text-muted-foreground">
                {currentQ + 1} / {sampleQuestions.length}
              </span>
            </div>
            <h3 className="text-lg font-display font-semibold text-foreground">
              {sampleQuestions[currentQ].question}
            </h3>
            <input
              className="w-full px-4 py-3 bg-muted/50 border border-border rounded-lg text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
              placeholder="Type your answer..."
              value={userAnswer}
              onChange={(e) => setUserAnswer(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && setShowAnswer(true)}
            />
            {!showAnswer ? (
              <Button onClick={() => setShowAnswer(true)} disabled={!userAnswer.trim()}>
                Check Answer
              </Button>
            ) : (
              <div className="space-y-3">
                <div className="bg-muted/50 rounded-lg p-4">
                  <p className="text-sm text-muted-foreground mb-1">Correct answer:</p>
                  <p className="text-foreground font-medium">{sampleQuestions[currentQ].answer}</p>
                </div>
                <p className="text-sm text-muted-foreground">Were you correct?</p>
                <div className="flex gap-3">
                  <Button onClick={() => submitAnswer(true)} className="gap-1">
                    <CheckCircle2 className="w-4 h-4" /> Yes
                  </Button>
                  <Button variant="outline" onClick={() => submitAnswer(false)} className="gap-1">
                    <XCircle className="w-4 h-4" /> No
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default ProgressTab;
