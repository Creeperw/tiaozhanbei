/**
 * 中医错题重做应用 - 单文件完整版
 * 
 * 文件结构：
 * 1. 类型定义 (Type Definitions)
 * 2. 常量配置 (Constants)
 * 3. 工具函数 (Utilities)
 * 4. 子组件 (Sub-components)
 *    - Header: 顶部导航
 *    - ReviewSession: 错题重做答题
 *    - MistakeLibrary: 错题库浏览
 *    - ReviewResult: 重做结果
 *    - Settings: 设置弹窗
 * 5. 主组件 (Main Component)
 * 6. 路由导出 (Route Export)
 */

import { useState, useMemo, useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  BookOpen, User, Settings2, X, ChevronDown, ChevronUp,
  CheckCircle2, XCircle, EyeOff, Send, Search, Filter,
  RotateCcw, Home, Target
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// =============================================================================
// 1. 类型定义
// =============================================================================

export type Difficulty = 'easy' | 'medium' | 'hard';
export type MistakeStatus = 'pending' | 'reviewing' | 'mastered';
export type Subject = 'math' | 'physics' | 'chemistry' | 'english' | 'chinese' | 'other';
export type Source = 'textbook' | 'exam' | 'practice' | 'mock' | 'other';

export interface Mistake {
  id: string;
  title: string;
  subject: Subject;
  difficulty: Difficulty;
  status: MistakeStatus;
  source: Source;
  wrongAnswer: string;
  correctAnswer: string;
  explanation: string;
  tags: string[];
  createdAt: string;
  reviewedAt?: string;
  reviewCount: number;
  imageUrl?: string;
}

export interface AppSettings {
  masteryThreshold: number;
  autoShowAnswer: boolean;
  shuffleQuestions: boolean;
  showExplanation: boolean;
}

// =============================================================================
// 2. 常量配置
// =============================================================================

const SETTINGS_KEY = "tcm-mistake-settings";

export const defaultSettings: AppSettings = {
  masteryThreshold: 2,
  autoShowAnswer: false,
  shuffleQuestions: true,
  showExplanation: true,
};

const SOURCE_LABELS: Record<Source, string> = {
  textbook: '教材',
  exam: '考试',
  practice: '练习',
  mock: '模拟',
  other: '其他',
};

const SOURCE_STYLES: Record<Source, string> = {
  textbook: 'bg-blue-100 text-blue-700',
  exam: 'bg-rose-100 text-rose-700',
  practice: 'bg-emerald-100 text-emerald-700',
  mock: 'bg-purple-100 text-purple-700',
  other: 'bg-gray-100 text-gray-600',
};

const STATUS_LABELS: Record<MistakeStatus, string> = {
  pending: '待复习',
  reviewing: '复习中',
  mastered: '已掌握',
};

// 示例数据
const MOCK_MISTAKES: Mistake[] = [
  {
    id: '1',
    title: '四气五味理论应用',
    subject: 'chinese',
    difficulty: 'medium',
    status: 'pending',
    source: 'textbook',
    wrongAnswer: '辛味药物都具有发散作用',
    correctAnswer: '辛味药物多具有发散、行气、活血作用，但并非所有辛味药都主发散',
    explanation: '辛味能散、能行，具有发散、行气、活血的作用。如麻黄辛散解表，川芎辛行气血。但需结合具体药物功效理解，不能一概而论。',
    tags: ['四气五味', '中药理论'],
    createdAt: '2026-07-25',
    reviewCount: 0,
  },
  {
    id: '2',
    title: '十二经脉循行路线',
    subject: 'chinese',
    difficulty: 'hard',
    status: 'reviewing',
    source: 'exam',
    wrongAnswer: '手太阴肺经起于中焦，止于食指桡侧',
    correctAnswer: '手太阴肺经起于中焦，向下联络大肠，回绕胃口，上行至肺，出腋下，沿上肢内侧前缘下行，止于拇指桡侧端',
    explanation: '肺经循行：起于中焦→下络大肠→还循胃口→上膈属肺→从肺系横出腋下→下循臑内→行少阴心主之前→下肘中→循臂内上骨下廉→入寸口→上鱼→循鱼际→出大指之端。',
    tags: ['经络', '循行路线'],
    createdAt: '2026-07-24',
    reviewedAt: '2026-07-27',
    reviewCount: 2,
  },
  {
    id: '3',
    title: '望舌诊病要点',
    subject: 'chinese',
    difficulty: 'easy',
    status: 'mastered',
    source: 'practice',
    wrongAnswer: '舌红主热证，舌淡主寒证',
    correctAnswer: '舌红主热证，舌淡白主气血两虚或阳虚',
    explanation: '舌色变化：淡白舌主气血两虚、阳虚；红舌主热证；绛舌主热入营血；紫舌主血瘀或热极。需结合舌形、苔色综合判断。',
    tags: ['望诊', '舌诊'],
    createdAt: '2026-07-20',
    reviewedAt: '2026-07-28',
    reviewCount: 3,
  },
  {
    id: '4',
    title: '方剂君臣佐使配伍',
    subject: 'chinese',
    difficulty: 'hard',
    status: 'pending',
    source: 'mock',
    wrongAnswer: '麻黄汤中桂枝为君药',
    correctAnswer: '麻黄汤中麻黄为君药，桂枝为臣药',
    explanation: '麻黄汤组成：麻黄（君）发汗解表、宣肺平喘；桂枝（臣）助麻黄发汗解表；杏仁（佐）降肺气、止咳平喘；甘草（使）调和诸药。',
    tags: ['方剂', '配伍'],
    createdAt: '2026-07-26',
    reviewCount: 0,
  },
  {
    id: '5',
    title: '脉象主病辨析',
    subject: 'chinese',
    difficulty: 'medium',
    status: 'reviewing',
    source: 'exam',
    wrongAnswer: '浮脉主表证，沉脉主里证',
    correctAnswer: '浮脉主表证，沉脉主里证，但需结合脉之有力无力辨虚实',
    explanation: '浮脉：轻取即得，重按稍减，主表证。沉脉：轻取不应，重按始得，主里证。脉有力为实证，无力为虚证。',
    tags: ['脉诊', '切诊'],
    createdAt: '2026-07-23',
    reviewedAt: '2026-07-27',
    reviewCount: 1,
  },
  {
    id: '6',
    title: '脏腑辨证要点',
    subject: 'chinese',
    difficulty: 'medium',
    status: 'pending',
    source: 'textbook',
    wrongAnswer: '肝火上炎证与肝阳上亢证症状相同',
    correctAnswer: '肝火上炎为实热证，肝阳上亢为本虚标实证，二者病机不同',
    explanation: '肝火上炎：头晕胀痛、面红目赤、急躁易怒、口苦咽干，属实热。肝阳上亢：眩晕耳鸣、头目胀痛、腰膝酸软，属本虚标实。',
    tags: ['脏腑辨证', '肝病'],
    createdAt: '2026-07-26',
    reviewCount: 0,
  },
];

// =============================================================================
// 3. 工具函数
// =============================================================================

/** 从 localStorage 读取设置 */
export function getSettings(): AppSettings {
  try {
    const saved = localStorage.getItem(SETTINGS_KEY);
    if (saved) {
      return { ...defaultSettings, ...JSON.parse(saved) };
    }
  } catch {
    // ignore
  }
  return defaultSettings;
}

/** 保存设置到 localStorage */
export function saveSettings(settings: AppSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // ignore
  }
}

/** 答案匹配算法 */
function checkAnswer(userAnswer: string, correctAnswer: string): boolean {
  const normalize = (s: string) => 
    s.toLowerCase()
     .replace(/[\s,，.。!！?？;；:：""''（）()\[\]]/g, '')
     .trim();
  
  const user = normalize(userAnswer);
  const correct = normalize(correctAnswer);
  
  // 完全匹配
  if (user === correct) return true;
  // 包含关系
  if (correct.includes(user) && user.length > 3) return true;
  if (user.includes(correct) && correct.length > 3) return true;
  
  // 关键词匹配（70%以上算对）
  const correctKeywords = correct.split(/[，,、]/).filter(k => k.length >= 2);
  const userKeywords = user.split(/[，,、]/).filter(k => k.length >= 2);
  
  if (correctKeywords.length > 0) {
    const matched = correctKeywords.filter(ck => 
      userKeywords.some(uk => uk.includes(ck) || ck.includes(uk))
    ).length;
    return matched / correctKeywords.length >= 0.7;
  }
  
  return false;
}

/** 获取状态样式 */
function getStatusStyle(status: MistakeStatus): string {
  if (status === 'mastered') return 'bg-emerald-100 text-emerald-700';
  if (status === 'reviewing') return 'bg-amber-100 text-amber-700';
  return 'bg-gray-100 text-gray-600';
}

// =============================================================================
// 4. 子组件
// =============================================================================

// ---------------------------------------------------------------------------
// Header - 顶部导航栏
// ---------------------------------------------------------------------------
interface HeaderProps {
  activeTab: 'review' | 'library';
  onTabChange: (tab: 'review' | 'library') => void;
  pendingCount: number;
  onSettingsClick?: () => void;
}

function Header({ activeTab, onTabChange, pendingCount, onSettingsClick }: HeaderProps) {
  return (
    <header className="sticky top-0 z-50 w-full border-b border-border/50 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
      <div className="container mx-auto px-4 h-16 flex items-center justify-between gap-4 max-w-5xl">
        {/* Logo */}
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-primary/15 text-primary">
            <BookOpen className="w-6 h-6" />
          </div>
          <h1 className="text-xl font-semibold text-foreground">中医错题本</h1>
        </div>

        {/* Tab 切换 */}
        <div className="flex items-center gap-1 bg-muted rounded-lg p-1">
          <TabButton
            active={activeTab === 'review'}
            onClick={() => onTabChange('review')}
            badge={pendingCount > 0 ? pendingCount : undefined}
          >
            错题重做
          </TabButton>
          <TabButton
            active={activeTab === 'library'}
            onClick={() => onTabChange('library')}
          >
            错题库
          </TabButton>
        </div>

        {/* 右侧按钮 */}
        <div className="flex items-center gap-2">
          <IconButton onClick={onSettingsClick} icon={<Settings2 className="w-5 h-5" />} />
          <IconButton icon={<User className="w-5 h-5" />} className="bg-accent/20 text-accent-foreground border-2 border-accent/30" />
        </div>
      </div>
    </header>
  );
}

function TabButton({ active, onClick, children, badge }: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  badge?: number;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "px-4 py-1.5 rounded-md text-sm font-medium transition-all relative",
        active
          ? "bg-primary text-primary-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {children}
      {badge !== undefined && badge > 0 && (
        <span className="ml-1.5 px-1.5 py-0.5 text-xs bg-accent text-accent-foreground rounded-full">
          {badge}
        </span>
      )}
    </button>
  );
}

function IconButton({ onClick, icon, className }: {
  onClick?: () => void;
  icon: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center justify-center w-9 h-9 rounded-full transition-colors",
        "bg-muted text-muted-foreground hover:bg-muted/80 hover:text-foreground",
        className
      )}
    >
      {icon}
    </button>
  );
}

// ---------------------------------------------------------------------------
// ReviewSession - 错题重做答题界面
// ---------------------------------------------------------------------------
interface ReviewSessionProps {
  mistake: Mistake;
  currentIndex: number;
  totalCount: number;
  onSubmit: (isCorrect: boolean) => void;
  onExit: () => void;
}

function ReviewSession({ mistake, currentIndex, totalCount, onSubmit, onExit }: ReviewSessionProps) {
  const [userAnswer, setUserAnswer] = useState("");
  const [showResult, setShowResult] = useState(false);
  const [isCorrect, setIsCorrect] = useState<boolean | null>(null);
  const [showExplanation, setShowExplanation] = useState(false);

  const progress = ((currentIndex + 1) / totalCount) * 100;

  const handleSubmit = () => {
    if (!userAnswer.trim()) return;
    const correct = checkAnswer(userAnswer, mistake.correctAnswer);
    setIsCorrect(correct);
    setShowResult(true);
  };

  const handleNext = () => {
    if (isCorrect !== null) {
      onSubmit(isCorrect);
      setUserAnswer("");
      setShowResult(false);
      setIsCorrect(null);
      setShowExplanation(false);
    }
  };

  return (
    <div className="animate-fade-in">
      {/* 进度条 */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-4 flex-1">
          <span className="text-sm font-medium text-muted-foreground">
            {currentIndex + 1} / {totalCount}
          </span>
          <Progress value={progress} className="flex-1 h-2" />
        </div>
        <button onClick={onExit} className="p-2 hover:bg-muted rounded-lg transition-colors">
          <X className="w-5 h-5 text-muted-foreground" />
        </button>
      </div>

      {/* 题目 */}
      <div className="bg-card rounded-2xl border border-border p-6 mb-6">
        <div className="flex items-center gap-2 mb-4">
          <span className="px-3 py-1 bg-primary/10 text-primary text-sm rounded-full">中医基础</span>
        </div>
        <h2 className="text-xl font-semibold text-foreground leading-relaxed">{mistake.title}</h2>
      </div>

      {/* 答题区 */}
      {!showResult && (
        <div className="mb-6 animate-fade-in">
          <label className="block text-sm font-medium text-foreground mb-2">你的答案</label>
          <Textarea
            value={userAnswer}
            onChange={(e) => setUserAnswer(e.target.value)}
            placeholder="请输入你的答案..."
            className="min-h-[120px] resize-none text-base"
          />
          <Button
            onClick={handleSubmit}
            disabled={!userAnswer.trim()}
            className="w-full mt-4 h-12 bg-primary hover:bg-primary/90 text-primary-foreground text-lg font-medium"
          >
            <Send className="w-5 h-5 mr-2" />提交答案
          </Button>
        </div>
      )}

      {/* 结果区 */}
      {showResult && (
        <div className="space-y-4 mb-6 animate-fade-in">
          {/* 判断结果 */}
          <ResultBanner isCorrect={isCorrect!} />

          {/* 答案对比 */}
          <div className="grid grid-cols-1 gap-4">
            <AnswerBox label="你的答案" answer={userAnswer || "未填写"} type={isCorrect ? 'correct' : 'wrong'} />
            <AnswerBox label="正确答案" answer={mistake.correctAnswer} type="correct" />
          </div>

          {/* 解析 */}
          <ExplanationBox explanation={mistake.explanation} show={showExplanation} onToggle={() => setShowExplanation(!showExplanation)} />

          {/* 下一题 */}
          <Button onClick={handleNext} className="w-full h-14 bg-primary hover:bg-primary/90 text-primary-foreground text-lg font-medium">
            {currentIndex < totalCount - 1 ? '下一题' : '查看结果'}
          </Button>
        </div>
      )}
    </div>
  );
}

function ResultBanner({ isCorrect }: { isCorrect: boolean }) {
  return (
    <div className={cn(
      "rounded-xl p-4 border",
      isCorrect ? "bg-emerald-50 border-emerald-200" : "bg-rose-50 border-rose-200"
    )}>
      <div className={cn(
        "text-lg font-semibold mb-2 flex items-center gap-2",
        isCorrect ? "text-emerald-700" : "text-rose-700"
      )}>
        {isCorrect ? (
          <><CheckCircle2 className="w-6 h-6" /> 回答正确</>
        ) : (
          <><XCircle className="w-6 h-6" /> 回答错误</>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        {isCorrect ? "恭喜你答对了！继续加油！" : "别灰心，查看解析巩固知识点"}
      </p>
    </div>
  );
}

function AnswerBox({ label, answer, type }: { label: string; answer: string; type: 'correct' | 'wrong' }) {
  const isCorrect = type === 'correct';
  return (
    <div className={cn(
      "rounded-xl p-4",
      isCorrect ? "bg-emerald-50 border border-emerald-200" : "bg-muted"
    )}>
      <div className={cn(
        "text-sm font-medium mb-2 flex items-center gap-2",
        isCorrect ? "text-emerald-700" : "text-muted-foreground"
      )}>
        {isCorrect && <CheckCircle2 className="w-4 h-4" />}
        {label}
      </div>
      <div className={cn("text-foreground", isCorrect ? "" : type === 'wrong' ? "text-rose-600" : "")}>
        {answer}
      </div>
    </div>
  );
}

function ExplanationBox({ explanation, show, onToggle }: { explanation: string; show: boolean; onToggle: () => void }) {
  return (
    <div className="bg-accent/20 rounded-xl border border-accent/30">
      <button onClick={onToggle} className="w-full flex items-center justify-between p-4 text-left">
        <span className="font-medium text-foreground flex items-center gap-2">
          <EyeOff className="w-4 h-4" />详细解析
        </span>
        {show ? <ChevronUp className="w-5 h-5 text-muted-foreground" /> : <ChevronDown className="w-5 h-5 text-muted-foreground" />}
      </button>
      {show && (
        <div className="px-4 pb-4 text-sm text-foreground leading-relaxed animate-fade-in">
          {explanation}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MistakeLibrary - 错题库
// ---------------------------------------------------------------------------
interface MistakeLibraryProps {
  mistakes: Mistake[];
  onReview: (mistake: Mistake) => void;
}

function MistakeLibrary({ mistakes, onReview }: MistakeLibraryProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState<Source | 'all'>('all');
  const [statusFilter, setStatusFilter] = useState<MistakeStatus | 'all'>('all');

  const filteredMistakes = useMemo(() => {
    return mistakes.filter((m) => {
      if (searchQuery && !m.title.toLowerCase().includes(searchQuery.toLowerCase())) return false;
      if (sourceFilter !== 'all' && m.source !== sourceFilter) return false;
      if (statusFilter !== 'all' && m.status !== statusFilter) return false;
      return true;
    });
  }, [mistakes, searchQuery, sourceFilter, statusFilter]);

  return (
    <div className="space-y-4">
      {/* 筛选栏 */}
      <Card className="border-border/60">
        <CardContent className="p-4 space-y-4">
          <SearchInput value={searchQuery} onChange={setSearchQuery} />
          <FilterBar
            sourceFilter={sourceFilter}
            statusFilter={statusFilter}
            onSourceChange={setSourceFilter}
            onStatusChange={setStatusFilter}
          />
        </CardContent>
      </Card>

      {/* 错题列表 */}
      <div className="space-y-3">
        {filteredMistakes.length === 0 ? (
          <EmptyState />
        ) : (
          filteredMistakes.map((mistake) => (
            <MistakeItem key={mistake.id} mistake={mistake} onReview={onReview} />
          ))
        )}
      </div>
    </div>
  );
}

function SearchInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="relative">
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
      <Input
        placeholder="搜索错题..."
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="pl-10"
      />
    </div>
  );
}

function FilterBar({ sourceFilter, statusFilter, onSourceChange, onStatusChange }: {
  sourceFilter: Source | 'all';
  statusFilter: MistakeStatus | 'all';
  onSourceChange: (s: Source | 'all') => void;
  onStatusChange: (s: MistakeStatus | 'all') => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <div className="flex items-center gap-2 mr-2">
        <Filter className="w-4 h-4 text-muted-foreground" />
        <span className="text-sm text-muted-foreground">筛选:</span>
      </div>
      
      {/* 来源筛选 */}
      <div className="flex gap-1">
        {(['all', 'textbook', 'exam', 'practice', 'mock', 'other'] as const).map((s) => (
          <FilterButton
            key={s}
            active={sourceFilter === s}
            onClick={() => onSourceChange(s)}
            label={s === 'all' ? '全部来源' : SOURCE_LABELS[s]}
          />
        ))}
      </div>

      {/* 状态筛选 */}
      <div className="flex gap-1">
        {(['all', 'pending', 'reviewing', 'mastered'] as const).map((s) => (
          <FilterButton
            key={s}
            active={statusFilter === s}
            onClick={() => onStatusChange(s)}
            label={s === 'all' ? '全部' : STATUS_LABELS[s]}
          />
        ))}
      </div>
    </div>
  );
}

function FilterButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <Button
      size="sm"
      variant={active ? 'default' : 'outline'}
      onClick={onClick}
      className={cn('text-xs', active && 'bg-primary text-primary-foreground')}
    >
      {label}
    </Button>
  );
}

function EmptyState() {
  return <div className="text-center py-12 text-muted-foreground">暂无符合条件的错题</div>;
}

function MistakeItem({ mistake, onReview }: { mistake: Mistake; onReview: (m: Mistake) => void }) {
  return (
    <Card className="border-border/60 hover:border-primary/40 transition-colors">
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <h3 className="font-medium text-foreground mb-2 line-clamp-1">{mistake.title}</h3>
            <div className="flex items-center gap-2">
              <Badge variant="secondary" className="text-xs">中医</Badge>
              <Badge className={cn('text-xs', SOURCE_STYLES[mistake.source])}>
                {SOURCE_LABELS[mistake.source]}
              </Badge>
              <Badge className={cn('text-xs', getStatusStyle(mistake.status))}>
                {STATUS_LABELS[mistake.status]}
              </Badge>
              <span className="text-xs text-muted-foreground">复习 {mistake.reviewCount} 次</span>
            </div>
          </div>
          <Button size="sm" onClick={() => onReview(mistake)} className="shrink-0 bg-primary hover:bg-primary/90">
            <RotateCcw className="w-4 h-4 mr-1" />重做
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// ReviewResult - 重做结果页
// ---------------------------------------------------------------------------
interface ReviewResultProps {
  results: { mistake: Mistake; isCorrect: boolean }[];
  onContinue: () => void;
  onFinish: () => void;
}

function ReviewResult({ results, onContinue, onFinish }: ReviewResultProps) {
  const correctCount = results.filter(r => r.isCorrect).length;
  const wrongCount = results.filter(r => !r.isCorrect).length;
  const accuracy = results.length > 0 ? Math.round((correctCount / results.length) * 100) : 0;
  const wrongItems = results.filter(r => !r.isCorrect);

  return (
    <div className="max-w-2xl mx-auto animate-fade-in">
      {/* 标题 */}
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-20 h-20 rounded-full bg-primary/10 mb-4">
          <Target className="w-10 h-10 text-primary" />
        </div>
        <h2 className="text-2xl font-semibold text-foreground mb-2">本次重做完成</h2>
        <p className="text-muted-foreground">坚持练习，温故知新</p>
      </div>

      {/* 统计 */}
      <div className="grid grid-cols-3 gap-4 mb-8">
        <StatCard icon={<CheckCircle2 className="w-5 h-5 text-emerald-600" />} label="做对" value={correctCount} color="emerald" />
        <StatCard icon={<XCircle className="w-5 h-5 text-rose-500" />} label="做错" value={wrongCount} color="rose" />
        <StatCard label="正确率" value={`${accuracy}%`} color="primary" />
      </div>

      {/* 错题回顾 */}
      {wrongItems.length > 0 && (
        <div className="mb-8">
          <h3 className="text-lg font-medium text-foreground mb-4 flex items-center gap-2">
            <XCircle className="w-5 h-5 text-rose-500" />错题回顾
          </h3>
          <div className="space-y-3">
            {wrongItems.map(({ mistake }) => (
              <div key={mistake.id} className="p-4 bg-rose-50/50 border border-rose-100 rounded-xl">
                <div className="font-medium text-foreground mb-2">{mistake.title}</div>
                <div className="text-sm text-rose-600 mb-1">你的答案：{mistake.wrongAnswer}</div>
                <div className="text-sm text-emerald-600">正确答案：{mistake.correctAnswer}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex gap-4">
        <Button variant="outline" onClick={onFinish} className="flex-1 h-12">
          <Home className="w-4 h-4 mr-2" />返回首页
        </Button>
        {wrongItems.length > 0 && (
          <Button onClick={onContinue} className="flex-1 h-12 bg-primary hover:bg-primary/90">
            <RotateCcw className="w-4 h-4 mr-2" />重做错题
          </Button>
        )}
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, color }: { icon?: React.ReactNode; label: string; value: string | number; color: string }) {
  const colorClasses: Record<string, string> = {
    emerald: 'text-emerald-600',
    rose: 'text-rose-500',
    primary: 'text-primary',
  };
  
  return (
    <Card className="border-border/50">
      <CardContent className="p-4 text-center">
        {icon && <div className="flex items-center justify-center gap-2 mb-2">{icon}<span className="text-sm text-muted-foreground">{label}</span></div>}
        {!icon && <div className="text-sm text-muted-foreground mb-2">{label}</div>}
        <div className={cn("text-3xl font-bold", colorClasses[color])}>{value}</div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Settings - 设置弹窗
// ---------------------------------------------------------------------------
interface SettingsProps {
  isOpen: boolean;
  onClose: () => void;
}

function Settings({ isOpen, onClose }: SettingsProps) {
  const [settings, setSettings] = useState<AppSettings>(defaultSettings);

  useEffect(() => {
    if (isOpen) setSettings(getSettings());
  }, [isOpen]);

  const handleSave = () => {
    saveSettings(settings);
    onClose();
  };

  const handleReset = () => {
    setSettings(defaultSettings);
    saveSettings(defaultSettings);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md bg-card rounded-2xl border border-border shadow-2xl animate-scale-in">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div className="flex items-center gap-2">
            <Settings2 className="w-5 h-5 text-primary" />
            <h2 className="text-lg font-semibold text-foreground">学习设置</h2>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-muted rounded-lg transition-colors">
            <X className="w-5 h-5 text-muted-foreground" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* 掌握阈值 */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <Label className="text-base font-medium text-foreground flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-primary" />掌握阈值
              </Label>
              <span className="text-sm font-semibold text-primary bg-primary/10 px-3 py-1 rounded-full">
                {settings.masteryThreshold} 次
              </span>
            </div>
            <p className="text-sm text-muted-foreground">错题做对多少次后自动标记为"已掌握"</p>
            <Slider
              value={[settings.masteryThreshold]}
              onValueChange={([v]) => setSettings(s => ({ ...s, masteryThreshold: v }))}
              min={1} max={5} step={1}
            />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>1次</span><span>2次</span><span>3次</span><span>4次</span><span>5次</span>
            </div>
          </div>

          <div className="h-px bg-border" />

          {/* 开关选项 */}
          <div className="space-y-4">
            <h3 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">重做选项</h3>
            <SwitchItem
              label="自动显示答案"
              description="提交后自动展开正确答案"
              checked={settings.autoShowAnswer}
              onCheckedChange={(v) => setSettings(s => ({ ...s, autoShowAnswer: v }))}
            />
            <SwitchItem
              label="随机出题顺序"
              description="打乱错题的复习顺序"
              checked={settings.shuffleQuestions}
              onCheckedChange={(v) => setSettings(s => ({ ...s, shuffleQuestions: v }))}
            />
            <SwitchItem
              label="显示详细解析"
              description="查看答案时自动展开解析"
              checked={settings.showExplanation}
              onCheckedChange={(v) => setSettings(s => ({ ...s, showExplanation: v }))}
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-4 border-t border-border bg-muted/30">
          <Button variant="ghost" size="sm" onClick={handleReset} className="text-muted-foreground hover:text-foreground">
            <RotateCcw className="w-4 h-4 mr-1.5" />恢复默认
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
            <Button size="sm" onClick={handleSave} className="bg-primary hover:bg-primary/90">保存设置</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SwitchItem({ label, description, checked, onCheckedChange }: {
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <div className="space-y-0.5">
        <Label className="text-sm font-medium text-foreground">{label}</Label>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

// =============================================================================
// 5. 主组件
// =============================================================================

function MistakeRedoApp() {
  const [activeTab, setActiveTab] = useState<'review' | 'library'>('review');
  const [mistakes, setMistakes] = useState<Mistake[]>(MOCK_MISTAKES);
  const [reviewQueue, setReviewQueue] = useState<Mistake[]>([]);
  const [currentReviewIndex, setCurrentReviewIndex] = useState(0);
  const [isReviewing, setIsReviewing] = useState(false);
  const [showResult, setShowResult] = useState(false);
  const [reviewResults, setReviewResults] = useState<{ mistake: Mistake; isCorrect: boolean }[]>([]);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<AppSettings>(getSettings());

  useEffect(() => setSettings(getSettings()), []);

  const pendingMistakes = useMemo(() => 
    mistakes.filter(m => m.status === 'pending' || m.status === 'reviewing'),
  [mistakes]);

  const startReviewSession = (selectedMistakes?: Mistake[]) => {
    const queue = selectedMistakes || pendingMistakes;
    if (queue.length === 0) return;
    setReviewQueue(queue);
    setCurrentReviewIndex(0);
    setReviewResults([]);
    setIsReviewing(true);
    setShowResult(false);
  };

  const submitAnswer = (isCorrect: boolean) => {
    const currentMistake = reviewQueue[currentReviewIndex];
    setReviewResults(prev => [...prev, { mistake: currentMistake, isCorrect }]);
    
    setMistakes(prev => prev.map(m => {
      if (m.id === currentMistake.id) {
        const newCount = m.reviewCount + 1;
        const shouldMaster = isCorrect && newCount >= settings.masteryThreshold;
        return {
          ...m,
          status: shouldMaster ? 'mastered' : 'reviewing',
          reviewCount: newCount,
          reviewedAt: new Date().toISOString().split('T')[0],
        };
      }
      return m;
    }));

    if (currentReviewIndex < reviewQueue.length - 1) {
      setCurrentReviewIndex(p => p + 1);
    } else {
      setIsReviewing(false);
      setShowResult(true);
    }
  };

  const endReviewSession = () => {
    setIsReviewing(false);
    setShowResult(false);
    setReviewQueue([]);
    setReviewResults([]);
    setCurrentReviewIndex(0);
  };

  const reviewFromLibrary = (mistake: Mistake) => startReviewSession([mistake]);

  // 首页内容
  const HomeView = () => (
    <div className="space-y-6">
      <div className="bg-gradient-to-br from-primary/10 to-accent/10 rounded-2xl p-8 text-center">
        <h2 className="text-2xl font-semibold text-foreground mb-2">开始错题重做</h2>
        <p className="text-muted-foreground mb-6">
          今日待复习 {pendingMistakes.length} 道错题，坚持练习，温故知新
        </p>
        <div className="flex items-center justify-center gap-4">
          <button
            onClick={() => startReviewSession()}
            disabled={pendingMistakes.length === 0}
            className="px-8 py-3 bg-primary text-primary-foreground rounded-xl font-medium
              hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors shadow-lg shadow-primary/20"
          >
            开始重做
          </button>
          {pendingMistakes.length > 0 && (
            <span className="text-sm text-muted-foreground">预计用时 {Math.ceil(pendingMistakes.length * 3)} 分钟</span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <StatCardSimple label="已掌握" value={mistakes.filter(m => m.status === 'mastered').length} color="primary" />
        <StatCardSimple label="复习中" value={mistakes.filter(m => m.status === 'reviewing').length} color="amber" />
        <StatCardSimple label="待复习" value={mistakes.filter(m => m.status === 'pending').length} color="emerald" />
      </div>
    </div>
  );

  function StatCardSimple({ label, value, color }: { label: string; value: number; color: string }) {
    const colors: Record<string, string> = {
      primary: 'text-primary',
      amber: 'text-amber-600',
      emerald: 'text-emerald-600',
    };
    return (
      <div className="bg-card rounded-xl p-4 border border-border text-center">
        <div className={cn("text-3xl font-bold mb-1", colors[color])}>{value}</div>
        <div className="text-sm text-muted-foreground">{label}</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <Header 
        activeTab={activeTab}
        onTabChange={setActiveTab}
        pendingCount={pendingMistakes.length}
        onSettingsClick={() => setIsSettingsOpen(true)}
      />
      <Settings isOpen={isSettingsOpen} onClose={() => { setIsSettingsOpen(false); setSettings(getSettings()); }} />
      <main className="container mx-auto px-4 py-6 max-w-5xl">
        {isReviewing ? (
          <ReviewSession
            mistake={reviewQueue[currentReviewIndex]}
            currentIndex={currentReviewIndex}
            totalCount={reviewQueue.length}
            onSubmit={submitAnswer}
            onExit={endReviewSession}
          />
        ) : showResult ? (
          <ReviewResult
            results={reviewResults}
            onContinue={() => startReviewSession()}
            onFinish={endReviewSession}
          />
        ) : activeTab === 'review' ? (
          <HomeView />
        ) : (
          <MistakeLibrary mistakes={mistakes} onReview={reviewFromLibrary} />
        )}
      </main>
    </div>
  );
}

// =============================================================================
// 6. 路由导出
// =============================================================================

export const Route = createFileRoute("/")({
  component: MistakeRedoApp,
});

export default MistakeRedoApp;
