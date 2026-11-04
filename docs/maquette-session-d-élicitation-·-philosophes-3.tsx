import React, { useState, useEffect, useMemo } from "react";

/* ============================== Types ============================== */

type Volet = "never" | "always" | "hypo";
type ClauseStatus = "proved" | "refuted" | "hypothesis";
type Clause = {
  qid: string;
  volet: Volet;
  text: string;
  status: ClauseStatus;
  tool: string;
  source: string;
  cluster?: string;
  formula?: string;
  runs?: string;
  cost?: string;
};
type LogEntry = { ts: string; label: string; tone: "answer" | "commit" | "proposal" | "revise" };
type WitnessStep = { active: string[]; fired?: string; narrative: string };
type Question = {
  id: string;
  kind: "static" | "mc";
  guideword: string;
  element: string;
  risk: number;
  cost: number;
  scenario: string;
  formula: string;
  vacuity: string;
  provenance: string;
  result: "impossible" | "possible";
  proof: string;
  clauseVolet: "never" | "always";
  neverText?: string;
  alwaysText?: string;
  normalText: string;
  cover: string[];
  cluster?: string;
  witness?: { steps: WitnessStep[]; cyclic?: boolean };
};

type ProposalStage = "closed" | "input" | "clarify" | "formula" | "verify" | "queued" | "verdict" | "committed";
type ProposalState = {
  stage: ProposalStage;
  text: string;
  option: string | null;
  phase: null | "queued" | "running";
  id?: string;
};

/* ============================== Données ============================== */

const COVERAGE_GROUPS = ["think", "hasL", "eat", "fork", "takeL", "takeR", "rel"];

const ROLE_COLORS: Record<string, string> = {
  automate: "#3b82f6",
  detention: "#f59e0b",
  critique: "#ef4444",
  ressource: "#10b981",
};

const CLUSTERS: Record<string, { cause: string; tone: string; title: string; fix: string; note: string }> = {
  A: {
    cause: "Défaut du MODÈLE",
    tone: "bg-rose-100 text-rose-700",
    title: "Verrou circulaire — impasse de l'anneau",
    fix: "Casser la symétrie : un philosophe asymétrique (prend d'abord sa fourchette droite). Les propriétés de récurrence tuées par l'impasse redeviendraient vertes.",
    note: "Même témoin (état hasL_1 = hasL_2 = hasL_3) partagé par toutes les violations du groupe.",
  },
  B: {
    cause: "Hypothèse de SPÉCIFICATION manquante",
    tone: "bg-amber-100 text-amber-700",
    title: "Équité de l'ordonnanceur non spécifiée",
    fix: "Écrire l'équité forte dans le volet hypothèses du contrat — ou accepter explicitement la famine. Ce n'est pas un bug du modèle.",
    note: "Témoin distinct : boucle du philosophe 2 seul ; takeR_1 activable une fois par tour mais jamais choisie (l'équité faible ne suffit pas, seule la forte répare).",
  },
};

const AMORCE = [
  {
    id: "think",
    elements: "think_1..3",
    role: "file d'attente",
    conf: 0.62,
    evidence: "le philosophe y « attend » ; jeton initial unique",
    wrong: true,
    options: ["état d'automate (attente)", "compteur", "ressource"],
  },
  { id: "hasL", elements: "hasL_1..3", role: "état intermédiaire (détention)", conf: 0.9, evidence: "« has + Left » ; conserve le jeton d'état" },
  { id: "eat", elements: "eat_1..3", role: "section critique", conf: 0.95, evidence: "« eat », précédé de deux acquisitions" },
  { id: "fork", elements: "fork_1..3", role: "ressource partagée (capacité 1)", conf: 0.97, evidence: "jeton initial unique ; consommée par deux voisins" },
  { id: "take", elements: "takeL_i / takeR_i", role: "acquisition (2 phases)", conf: 0.95, evidence: "consomme think + fork, produit hasL puis eat" },
  { id: "rel", elements: "rel_i", role: "libération", conf: 0.97, evidence: "produit exactement ce que takeL/takeR consomment" },
  {
    id: "motif",
    elements: "motif global",
    role: "anneau de processus / ressources alternées (symétrie d'ordre 3)",
    conf: 0.93,
    evidence: "noms 1/2/3 + disposition en arc de cercle (coordonnées PNML)",
  },
];

const DEADLOCK_TRACE: WitnessStep[] = [
  { active: ["think1", "think2", "think3", "fork1", "fork2", "fork3"], narrative: "État initial : les trois philosophes réfléchissent, les fourchettes sont posées." },
  { active: ["hasL1", "think2", "think3", "fork2", "fork3"], fired: "takeL_1", narrative: "Le philosophe 1 saisit sa fourchette gauche (fork_1)." },
  { active: ["hasL1", "hasL2", "think3", "fork3"], fired: "takeL_2", narrative: "Le philosophe 2 saisit la sienne (fork_2)." },
  {
    active: ["hasL1", "hasL2", "hasL3"],
    fired: "takeL_3",
    narrative: "Le philosophe 3 saisit fork_3. Chacun attend la fourchette de son voisin : verrou circulaire. IMPASSE — aucune transition activable.",
  },
];

const STARVATION_TRACE: WitnessStep[] = [
  { active: ["hasL1", "eat2", "think3", "fork1"], narrative: "Le philosophe 2 mange en boucle ; le 1 attend, fourchette gauche en main (fork_1)." },
  {
    active: ["hasL1", "think2", "think3", "fork1", "fork2", "fork3"],
    fired: "rel_2",
    narrative: "Le 2 repose ses couverts : fork_2 est libre un instant — takeR_1 devient activable pour le philosophe 1…",
  },
  { active: ["hasL1", "hasL2", "think3", "fork1", "fork3"], fired: "takeL_2", narrative: "…mais l'ordonnanceur redonne aussitôt fork_2 au philosophe 2." },
  {
    active: ["hasL1", "eat2", "think3", "fork1"],
    fired: "takeR_2",
    narrative: "Le 2 mange à nouveau. Retour à l'étape 1 : le philosophe 1 meurt de faim alors que sa chance revient à chaque tour.",
  },
];

const QUESTIONS: Question[] = [
  {
    id: "S1",
    kind: "static",
    guideword: "More",
    element: "ressource fork_i",
    risk: 5,
    cost: 0,
    scenario: "Deux philosophes peuvent-ils détenir la même fourchette au même moment ?",
    formula: "fork_i + hasL_i + eat_i + eat_(i-1) = 1 (invariant de places)",
    vacuity: "EF (eat_1) — satisfaite",
    provenance: "guideword « More » × rôle ressource partagée (confiance 0,97)",
    result: "impossible",
    proof: "Confirmé impossible par l'invariant statique (v·C = 0 + marquage initial) — 3 ms, sans exploration d'états.",
    clauseVolet: "never",
    neverText: "Deux philosophes ne détiennent jamais la même fourchette",
    normalText: "Le partage occasionnel d'une fourchette est acceptable dans le système réel",
    cover: ["eat", "fork"],
  },
  {
    id: "S2",
    kind: "static",
    guideword: "As well as",
    element: "ressource fork_i",
    risk: 5,
    cost: 0,
    scenario: "Une fourchette peut-elle disparaître ou être dupliquée ?",
    formula: "Σ jetons = 6 (constante, invariants)",
    vacuity: "—",
    provenance: "guideword « As well as » × conservation de ressource",
    result: "impossible",
    proof: "Confirmé : la somme des invariants vaut 2N = 6 dans tout marquage atteignable — 2 ms.",
    clauseVolet: "never",
    neverText: "Une fourchette n'est jamais perdue ni dupliquée (Σ jetons = 6)",
    normalText: "Le système est ouvert : des fourchettes peuvent être ajoutées ou retirées",
    cover: ["fork"],
  },
  {
    id: "S3",
    kind: "static",
    guideword: "More",
    element: "états du philosophe i",
    risk: 4,
    cost: 0,
    scenario: "Un philosophe peut-il être dans deux états à la fois (réfléchir et manger) ?",
    formula: "think_i + hasL_i + eat_i = 1 (invariant d'état)",
    vacuity: "—",
    provenance: "guideword « More » × rôle état d'automate",
    result: "impossible",
    proof: "Confirmé par l'invariant de machine à états — 2 ms.",
    clauseVolet: "never",
    neverText: "Un philosophe est exactement dans un état à la fois",
    normalText: "Le chevauchement d'états est toléré dans le système réel",
    cover: ["think", "hasL", "eat"],
  },
  {
    id: "S4",
    kind: "static",
    guideword: "Reverse",
    element: "flux takeL → takeR → eat",
    risk: 4,
    cost: 0,
    scenario: "Un philosophe peut-il manger sans avoir pris ses deux fourchettes ?",
    formula: "seul producteur de eat_i : takeR_i (précondition hasL_i)",
    vacuity: "—",
    provenance: "guideword « Reverse » × flux d'acquisition",
    result: "impossible",
    proof: "Confirmé structurellement : eat_i n'a qu'un producteur, de précondition hasL_i.",
    clauseVolet: "never",
    neverText: "On ne mange qu'après avoir pris les deux fourchettes",
    normalText: "Le modèle abstrait volontairement le détail des acquisitions",
    cover: ["eat", "takeR"],
  },
  {
    id: "S5",
    kind: "static",
    guideword: "Early",
    element: "flux d'acquisition",
    risk: 3,
    cost: 0,
    scenario: "La fourchette droite peut-elle être prise avant la gauche ?",
    formula: "précondition de takeR_i : hasL_i (produit par takeL_i)",
    vacuity: "—",
    provenance: "guideword « Early » × flux d'acquisition",
    result: "impossible",
    proof: "Confirmé structurellement : takeR_i exige hasL_i.",
    clauseVolet: "never",
    neverText: "La fourchette droite n'est jamais prise avant la gauche",
    normalText: "L'ordre d'acquisition est libre dans le système réel",
    cover: ["hasL", "takeL", "takeR"],
  },
  {
    id: "S6",
    kind: "static",
    guideword: "Early",
    element: "libération rel_i",
    risk: 3,
    cost: 0,
    scenario: "Peut-on rendre une fourchette sans avoir mangé ?",
    formula: "producteurs de fork_j : { rel_j, rel_(j-1) }, précondition eat",
    vacuity: "—",
    provenance: "guideword « Early » × rôle libération",
    result: "impossible",
    proof: "Confirmé structurellement : tout producteur de fork_j a eat en précondition.",
    clauseVolet: "never",
    neverText: "On ne rend une fourchette qu'après avoir mangé",
    normalText: "La restitution anticipée est possible (timeout, panne)",
    cover: ["fork", "rel"],
  },
  {
    id: "M1",
    kind: "mc",
    guideword: "No (global)",
    element: "motif anneau",
    risk: 5,
    cost: 1,
    scenario: "Le système peut-il se figer tout entier — plus personne ne peut bouger ?",
    formula: "EF DEADLOCK",
    vacuity: "—",
    provenance: "guideword « No » × motif anneau de ressources",
    result: "possible",
    proof: "EF DEADLOCK = vrai — état atteint en 3 tirs (14 états, 27 arcs, 0,4 s d'exploration).",
    clauseVolet: "never",
    neverText: "Le système ne se fige jamais (impasse impossible)",
    normalText: "L'impasse est débloquée par intervention externe (watchdog, opérateur)",
    cover: ["takeL", "takeR", "rel"],
    cluster: "A",
    witness: { steps: DEADLOCK_TRACE },
  },
  {
    id: "M2",
    kind: "mc",
    guideword: "Less",
    element: "flux partiel hasL_i",
    risk: 4,
    cost: 2,
    scenario: "Un philosophe peut-il rester indéfiniment fourchette en main, sans jamais finir son geste ?",
    formula: "AG (hasL_1 → AF (eat_1 ∨ think_1))",
    vacuity: "EF (hasL_1) — satisfaite",
    provenance: "guideword « Less » × flux d'acquisition partiel",
    result: "possible",
    proof: "Faux — gelé depuis l'état d'impasse (convention : marquage d'impasse = figé pour toujours). Même témoin que l'impasse.",
    clauseVolet: "never",
    neverText: "Un philosophe ne reste pas indéfiniment fourchette en main",
    normalText: "Un philosophe peut garder sa fourchette indéfiniment (réunion, pause)",
    cover: ["hasL", "takeR"],
    cluster: "A",
    witness: { steps: DEADLOCK_TRACE },
  },
  {
    id: "M4",
    kind: "mc",
    guideword: "No",
    element: "section critique eat_i",
    risk: 4,
    cost: 2,
    scenario: "Un philosophe peut-il rester bloqué en section critique, couverts en main, sans jamais pouvoir sortir ?",
    formula: "AG (eat_1 → AF think_1)",
    vacuity: "EF (eat_1) — satisfaite",
    provenance: "guideword « No » × rôle section critique",
    result: "impossible",
    proof: "Vrai sans aucune hypothèse d'équité : depuis eat_1, le système se verrouille autour du mangeur jusqu'à ce que rel_1 devienne la seule transition activable — sortie forcée (vivacité structurelle).",
    clauseVolet: "always",
    alwaysText: "Un philosophe en section critique finit toujours par en sortir (sans hypothèse d'équité)",
    normalText: "Un long séjour en section critique est acceptable",
    cover: ["eat", "rel"],
  },
  {
    id: "M5",
    kind: "mc",
    guideword: "No",
    element: "ressource fork_i",
    risk: 3,
    cost: 1,
    scenario: "Une fourchette peut-elle rester captive pour toujours, plus jamais disponible ?",
    formula: "AG EF (fork_1 = 1)",
    vacuity: "EF (fork_1 = 1) — satisfaite (état initial)",
    provenance: "guideword « No » × rôle ressource partagée",
    result: "possible",
    proof: "Faux — depuis l'état d'impasse, aucune fourchette ne redevient disponible. Témoin : l'impasse (clustering avec M1/M2).",
    clauseVolet: "always",
    alwaysText: "Toute fourchette finit toujours par redevenir disponible",
    normalText: "Une fourchette peut être durablement monopolisée",
    cover: ["fork"],
    cluster: "A",
    witness: { steps: DEADLOCK_TRACE },
  },
  {
    id: "M3",
    kind: "mc",
    guideword: "Late",
    element: "vivacité individuelle",
    risk: 3,
    cost: 3,
    scenario: "Quelqu'un peut-il mourir de faim alors que tout reste activable et que le système continue de vivre ?",
    formula: "AG AF (eat_1)",
    vacuity: "EF (eat_1) — satisfaite",
    provenance: "guideword « Late » × motif anneau",
    result: "possible",
    proof: "Faux sans équité — témoin ci-dessous. Mais vrai sous équité forte (l'équité faible ne suffit pas : takeR_1 n'est activable qu'une fois par tour). Le défaut est une hypothèse manquante dans la spécification, pas un bug du modèle.",
    clauseVolet: "never",
    neverText: "Personne ne meurt de faim tant que le système vit (sans hypothèse d'équité)",
    normalText: "L'ordonnanceur est équitable dans le déploiement réel (équité forte)",
    cover: ["think", "eat"],
    cluster: "B",
    witness: { steps: STARVATION_TRACE, cyclic: true },
  },
];

const STATIC_QS = QUESTIONS.filter((q) => q.kind === "static");
const MC_QS = [...QUESTIONS.filter((q) => q.kind === "mc")].sort((a, b) => b.risk - a.risk || a.cost - b.cost);

/* ============================== Vue du réseau ============================== */

const PHILOS = [
  { id: 1, x: 320, y: 78 },
  { id: 2, x: 540, y: 332 },
  { id: 3, x: 120, y: 332 },
];
const FORKS = [
  { id: 2, x: 415, y: 195 },
  { id: 3, x: 320, y: 500 },
  { id: 1, x: 225, y: 195 },
];
const PLACE_OFF: Record<string, number> = { think: 0, hasL: 48, eat: 96 };

const TRANSITIONS = [
  { id: "takeL_1", x: 254, y: 102, lx: 254, ly: 90, anchor: "middle" },
  { id: "takeR_1", x: 386, y: 150, lx: 400, ly: 154, anchor: "start" },
  { id: "rel_1", x: 320, y: 240, lx: 320, ly: 258, anchor: "middle" },
  { id: "takeL_2", x: 474, y: 356, lx: 474, ly: 374, anchor: "middle" },
  { id: "takeR_2", x: 606, y: 404, lx: 606, ly: 422, anchor: "middle" },
  { id: "rel_2", x: 540, y: 492, lx: 540, ly: 510, anchor: "middle" },
  { id: "takeL_3", x: 186, y: 356, lx: 198, ly: 344, anchor: "middle" },
  { id: "takeR_3", x: 54, y: 404, lx: 54, ly: 422, anchor: "middle" },
  { id: "rel_3", x: 120, y: 492, lx: 120, ly: 510, anchor: "middle" },
];

const NODES: Record<string, { x: number; y: number; r?: number; hw?: number; hh?: number }> = {};
PHILOS.forEach((p) =>
  (["think", "hasL", "eat"] as const).forEach((pl) => {
    NODES[`${pl}_${p.id}`] = { x: p.x, y: p.y + PLACE_OFF[pl], r: 13 };
  })
);
FORKS.forEach((f) => {
  NODES[`fork_${f.id}`] = { x: f.x, y: f.y, r: 16 };
});
TRANSITIONS.forEach((t) => {
  NODES[t.id] = { x: t.x, y: t.y, hw: 10, hh: 6 };
});

const ARCS: { src: string; dst: string; cx?: number; cy?: number }[] = [
  { src: "think_1", dst: "takeL_1" },
  { src: "fork_1", dst: "takeL_1" },
  { src: "takeL_1", dst: "hasL_1" },
  { src: "hasL_1", dst: "takeR_1" },
  { src: "fork_2", dst: "takeR_1" },
  { src: "takeR_1", dst: "eat_1" },
  { src: "eat_1", dst: "rel_1" },
  { src: "rel_1", dst: "think_1", cx: 234, cy: 170 },
  { src: "rel_1", dst: "fork_1" },
  { src: "rel_1", dst: "fork_2" },
  { src: "think_2", dst: "takeL_2" },
  { src: "fork_2", dst: "takeL_2" },
  { src: "takeL_2", dst: "hasL_2" },
  { src: "hasL_2", dst: "takeR_2" },
  { src: "fork_3", dst: "takeR_2", cx: 470, cy: 540 },
  { src: "takeR_2", dst: "eat_2" },
  { src: "eat_2", dst: "rel_2" },
  { src: "rel_2", dst: "think_2", cx: 610, cy: 410 },
  { src: "rel_2", dst: "fork_2", cx: 480, cy: 320 },
  { src: "rel_2", dst: "fork_3" },
  { src: "think_3", dst: "takeL_3" },
  { src: "fork_3", dst: "takeL_3" },
  { src: "takeL_3", dst: "hasL_3" },
  { src: "hasL_3", dst: "takeR_3" },
  { src: "fork_1", dst: "takeR_3", cx: 110, cy: 290 },
  { src: "takeR_3", dst: "eat_3" },
  { src: "eat_3", dst: "rel_3" },
  { src: "rel_3", dst: "think_3", cx: 50, cy: 410 },
  { src: "rel_3", dst: "fork_1", cx: 180, cy: 320 },
  { src: "rel_3", dst: "fork_3" },
];

function arcPath(a: { src: string; dst: string; cx?: number; cy?: number }): string {
  const s = NODES[a.src];
  const d = NODES[a.dst];
  const vx = a.cx !== undefined ? d.x - a.cx : d.x - s.x;
  const vy = a.cy !== undefined ? d.y - a.cy : d.y - s.y;
  const len = Math.hypot(vx, vy) || 1;
  const ux = vx / len;
  const uy = vy / len;
  let ex: number;
  let ey: number;
  if (d.r) {
    ex = d.x - ux * (d.r + 2);
    ey = d.y - uy * (d.r + 2);
  } else {
    const t = Math.min(Math.abs(ux) > 1e-6 ? 12 / Math.abs(ux) : 1e9, Math.abs(uy) > 1e-6 ? 8 / Math.abs(uy) : 1e9);
    ex = d.x - ux * t;
    ey = d.y - uy * t;
  }
  return a.cx !== undefined ? `M ${s.x},${s.y} Q ${a.cx},${a.cy} ${ex},${ey}` : `M ${s.x},${s.y} L ${ex},${ey}`;
}

function NetView({
  active,
  gained,
  covered,
  hover,
  onPlaceClick,
  selectedGroup,
  firedT,
}: {
  active: string[];
  gained: string[];
  covered?: Set<string>;
  hover?: string[] | null;
  onPlaceClick?: (group: string) => void;
  selectedGroup?: string | null;
  firedT?: string;
}) {
  const has = (k: string) => active.includes(k);
  const isNew = (k: string) => gained.includes(k);
  const groupOf = (k: string) =>
    k.startsWith("think") ? "think" : k.startsWith("hasL") ? "hasL" : k.startsWith("eat") ? "eat" : "fork";
  const dim = (k: string) => !!covered && !covered.has(groupOf(k)) && !(hover || []).includes(groupOf(k));
  const glow = (k: string) => (hover || []).includes(groupOf(k)) || selectedGroup === groupOf(k);
  return (
    <svg viewBox="0 0 660 560" className="w-full rounded-xl border border-slate-200 bg-slate-50">
      <defs>
        <marker id="pn-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="#94a3b8" />
        </marker>
      </defs>
      <text x="16" y="24" fontSize="11" fill="#94a3b8">
        Philosophes-3 · P/T · 12 places · 9 transitions · 30 arcs
      </text>
      {ARCS.map((a) => (
        <path
          key={a.src + ">" + a.dst}
          d={arcPath(a)}
          fill="none"
          stroke="#94a3b8"
          strokeWidth="1.4"
          markerEnd="url(#pn-arrow)"
        />
      ))}
      {TRANSITIONS.map((t) => {
        const g = t.id.startsWith("takeL") ? "takeL" : t.id.startsWith("takeR") ? "takeR" : "rel";
        const glowT =
          (hover || []).includes(g) || selectedGroup === g || (selectedGroup === "take" && t.id.startsWith("take"));
        const dimT = !!covered && !covered.has(g) && !glowT;
        const isFired = firedT === t.id;
        return (
          <g key={t.id}>
            {glowT && (
              <rect x={t.x - 14} y={t.y - 10} width="28" height="20" rx="4" fill="none" stroke="#6366f1" strokeWidth="3" opacity="0.9" />
            )}
            {isFired && (
              <rect x={t.x - 15} y={t.y - 11} width="30" height="22" rx="5" fill="none" stroke="#f59e0b" strokeWidth="3" opacity="0.95" />
            )}
            <rect
              x={t.x - 10}
              y={t.y - 6}
              width="20"
              height="12"
              rx="2"
              fill={isFired ? "#f59e0b" : "#334155"}
              opacity={dimT ? 0.25 : 1}
              className={onPlaceClick ? "cursor-pointer" : ""}
              onClick={() => onPlaceClick?.(t.id.startsWith("rel") ? "rel" : "take")}
            />
            <text x={t.lx} y={t.ly} textAnchor={t.anchor} fontSize="9" fill="#475569" fontFamily="monospace" opacity={dimT ? 0.4 : 1}>
              {t.id}
            </text>
          </g>
        );
      })}
      {PHILOS.map((p) => (
        <g key={p.id}>
          <text x={p.x} y={p.y - 16} textAnchor="middle" fontSize="12" fontWeight="600" fill="#475569">
            Philosophe {p.id}
          </text>
          {(["think", "hasL", "eat"] as const).map((pl) => {
            const k = `${pl}${p.id}`;
            const cy = p.y + PLACE_OFF[pl];
            const col = pl === "think" ? ROLE_COLORS.automate : pl === "hasL" ? ROLE_COLORS.detention : ROLE_COLORS.critique;
            return (
              <g key={pl}>
                {glow(k) && <circle cx={p.x} cy={cy} r={18} fill="none" stroke="#6366f1" strokeWidth="3" opacity="0.9" />}
                {isNew(k) && <circle cx={p.x} cy={cy} r={19} fill="none" stroke="#f59e0b" strokeWidth="2.5" opacity="0.85" />}
                <circle
                  cx={p.x}
                  cy={cy}
                  r={13}
                  fill={has(k) ? col : "#ffffff"}
                  stroke={col}
                  strokeWidth="2"
                  opacity={dim(k) ? 0.3 : 1}
                  className={onPlaceClick ? "cursor-pointer" : ""}
                  onClick={() => onPlaceClick?.(groupOf(k))}
                />
                <text x={p.x + 20} y={cy + 4} fontSize="10" fill="#64748b" fontFamily="monospace" opacity={dim(k) ? 0.4 : 1}>
                  {pl}_{p.id}
                </text>
              </g>
            );
          })}
        </g>
      ))}
      {FORKS.map((f) => {
        const k = `fork${f.id}`;
        return (
          <g key={f.id}>
            {glow(k) && <circle cx={f.x} cy={f.y} r={19} fill="none" stroke="#6366f1" strokeWidth="3" opacity="0.9" />}
            {isNew(k) && <circle cx={f.x} cy={f.y} r={20} fill="none" stroke="#f59e0b" strokeWidth="2.5" opacity="0.85" />}
            <rect
              x={f.x - 11}
              y={f.y - 11}
              width="22"
              height="22"
              transform={`rotate(45 ${f.x} ${f.y})`}
              fill={has(k) ? ROLE_COLORS.ressource : "#ffffff"}
              stroke={ROLE_COLORS.ressource}
              strokeWidth="2"
              opacity={dim(k) ? 0.3 : 1}
              className={onPlaceClick ? "cursor-pointer" : ""}
              onClick={() => onPlaceClick?.("fork")}
            />
            <text x={f.x} y={f.y + 28} textAnchor="middle" fontSize="10" fill="#64748b" fontFamily="monospace" opacity={dim(k) ? 0.4 : 1}>
              fork_{f.id}
            </text>
          </g>
        );
      })}
      <g>
        <rect x="450" y="12" width="200" height="76" rx="8" fill="#ffffff" stroke="#e2e8f0" />
        <circle cx="464" cy="28" r="5" fill={ROLE_COLORS.automate} />
        <text x="474" y="32" fontSize="9" fill="#64748b">état d'automate</text>
        <circle cx="464" cy="44" r="5" fill={ROLE_COLORS.detention} />
        <text x="474" y="48" fontSize="9" fill="#64748b">détention</text>
        <circle cx="464" cy="60" r="5" fill={ROLE_COLORS.critique} />
        <text x="474" y="64" fontSize="9" fill="#64748b">section critique</text>
        <rect x="560" y="24" width="14" height="8" rx="1" fill="#334155" />
        <text x="578" y="32" fontSize="9" fill="#64748b">transition</text>
        <rect x="562" y="42" width="8" height="8" transform="rotate(45 566 46)" fill={ROLE_COLORS.ressource} />
        <text x="578" y="48" fontSize="9" fill="#64748b">ressource</text>
        <rect x="560" y="58" width="14" height="8" rx="1" fill="#f59e0b" />
        <text x="578" y="64" fontSize="9" fill="#64748b">transition tirée</text>
      </g>
    </svg>
  );
}

function WitnessPlayer({ steps, cyclic }: { steps: WitnessStep[]; cyclic?: boolean }) {
  const [i, setI] = useState(0);
  const step = steps[i];
  const gained = i > 0 ? steps[i].active.filter((k) => !steps[i - 1].active.includes(k)) : [];
  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Contre-exemple — lecture pas à pas</span>
        <span className="text-xs font-mono text-slate-500">
          {cyclic && i === steps.length - 1 ? "cycle ⟳" : `étape ${i + 1}/${steps.length}`}
        </span>
      </div>
      <NetView active={step.active} gained={gained} firedT={step.fired} />
      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={() => setI(Math.max(0, i - 1))}
          disabled={i === 0}
          aria-label="Étape précédente du contre-exemple"
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 disabled:opacity-40 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
        >
          ‹
        </button>
        <button
          onClick={() => setI((i + 1) % steps.length)}
          aria-label="Étape suivante du contre-exemple"
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
        >
          {cyclic && i === steps.length - 1 ? "revenir à l'étape 1 ⟳" : "suivant ›"}
        </button>
        {step.fired && (
          <span className="rounded-md bg-slate-800 px-2 py-0.5 font-mono text-xs text-white">{step.fired}</span>
        )}
      </div>
      <p className="mt-2 text-sm text-slate-700">{step.narrative}</p>
    </div>
  );
}

/* ============================== Carte-question ============================== */

const toneClasses: Record<string, string> = {
  ok: "bg-emerald-50 border-emerald-200 text-emerald-800",
  bad: "bg-rose-50 border-rose-200 text-rose-800",
  warn: "bg-amber-50 border-amber-200 text-amber-900",
  info: "bg-indigo-50 border-indigo-200 text-indigo-800",
};

function QuestionCard({
  q,
  priority,
  answeredKind,
  coveredBy,
  onAnswer,
  onCommit,
  onRevise,
  onHover,
}: {
  q: Question;
  priority?: number;
  answeredKind?: string;
  coveredBy?: string;
  onAnswer: (id: string, kind: string) => void;
  onCommit: (q: Question, volet: Volet) => void;
  onRevise?: (id: string) => void;
  onHover?: (cover: string[] | null) => void;
}) {
  const [phase, setPhase] = useState<null | "queued" | "running" | "done">(null);
  const [pending, setPending] = useState<Volet | null>(null);
  const [kind, setKind] = useState<string>("");
  const [committed, setCommitted] = useState<Volet | null>(null);
  const [open, setOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [edited, setEdited] = useState(q.formula);
  const [variantQueued, setVariantQueued] = useState(false);

  useEffect(() => {
    if (answeredKind === undefined && (phase !== null || committed !== null || kind !== "")) {
      setPhase(null);
      setPending(null);
      setKind("");
      setCommitted(null);
      setVariantQueued(false);
      setEdited(q.formula);
    }
  }, [answeredKind]);

  useEffect(() => {
    if (phase === "queued") {
      const t = setTimeout(() => setPhase("running"), q.kind === "static" ? 450 : 800);
      return () => clearTimeout(t);
    }
    if (phase === "running") {
      const t = setTimeout(() => {
        setPhase("done");
        onAnswer(q.id, kind);
        if (pending) {
          onCommit(q, pending);
          setCommitted(pending);
        }
      }, q.kind === "static" ? 550 : 1700);
      return () => clearTimeout(t);
    }
  }, [phase]);

  const start = (volet: Volet | null, k: string) => {
    setPending(volet);
    setKind(k);
    setPhase("queued");
  };

  const answerNormal = () => {
    onAnswer(q.id, "normal");
    onCommit(q, "hypo");
    setCommitted("hypo");
    setKind("normal");
  };

  const commitVolet = (v: Volet) => {
    onCommit(q, v);
    setCommitted(v);
  };

  const answered = answeredKind !== undefined;
  const verdictTone = q.result === "impossible" ? "ok" : q.cluster === "B" ? "warn" : "bad";
  const phaseLabel =
    phase === "queued"
      ? q.kind === "static"
        ? "Vérification statique soumise…"
        : "En file d'attente (budget 30 s)…"
      : phase === "running"
        ? q.kind === "static"
          ? "Analyse statique en cours…"
          : "Exploration d'états en cours — LoLA · 14 états déjà atteints…"
        : "";

  const vacuityOk = q.vacuity.includes("satisfaite");
  const vacuityNA = q.vacuity === "—";

  return (
    <div
      className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
      onMouseEnter={() => onHover?.(q.cover)}
      onMouseLeave={() => onHover?.(null)}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-md bg-amber-100 px-2 py-0.5 font-medium text-amber-800">mot-guid : {q.guideword}</span>
        <span className="rounded-md bg-slate-100 px-2 py-0.5 font-medium text-slate-600">{q.element}</span>
        <span className={`rounded-md px-2 py-0.5 font-medium ${q.kind === "static" ? "bg-indigo-100 text-indigo-700" : "bg-violet-100 text-violet-700"}`}>
          {q.kind === "static" ? "statique — gratuit" : `MC · risque ${q.risk}/5 × coût ${q.cost}/3`}
        </span>
        {priority && (
          <span className="rounded-md bg-slate-800 px-2 py-0.5 font-medium text-white">priorité #{priority}</span>
        )}
        <span
          className={`rounded-md px-2 py-0.5 font-medium ${vacuityOk ? "bg-emerald-100 text-emerald-700" : vacuityNA ? "bg-slate-100 text-slate-400" : "bg-rose-100 text-rose-700"}`}
          title={q.vacuity}
        >
          non-vacuité {vacuityOk ? "✔ satisfaite" : vacuityNA ? "· sans objet" : "✗ à traiter"}
        </span>
        {coveredBy && (
          <span className="rounded-md bg-violet-600 px-2 py-0.5 font-medium text-white">≡ {coveredBy} — couverte</span>
        )}
      </div>
      <p className="mt-3 text-sm font-medium leading-relaxed text-slate-800">{q.scenario}</p>
      <button onClick={() => setOpen(!open)} className="mt-2 text-xs font-medium text-indigo-600 hover:underline">
        {open ? "masquer" : "voir"} la formule MCC, sa lecture naturelle et la provenance
      </button>
      {open && (
        <div className="mt-2 rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
          <div className="font-mono text-slate-800">{q.formula}</div>
          <div className="mt-1.5 border-t border-slate-200 pt-1.5 text-slate-700">
            <b>Lecture :</b> {Q_READINGS[q.id]?.reading}
          </div>
          <div className="mt-0.5 text-slate-500">
            <b>Ne dit pas :</b> {Q_READINGS[q.id]?.notSays}
          </div>
          <div className="mt-1.5 border-t border-slate-200 pt-1">Non-vacuité : {q.vacuity}</div>
          <div>Origine : {q.provenance}</div>
          <div className="mt-2 border-t border-slate-200 pt-2">
            {!editOpen ? (
              <button
                onClick={() => setEditOpen(true)}
                className="text-indigo-600 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
              >
                ✎ éditer la formule (mode avancé)
              </button>
            ) : (
              <div>
                <input
                  value={edited}
                  onChange={(e) => {
                    setEdited(e.target.value);
                    setVariantQueued(false);
                  }}
                  aria-label={`Formule avancée — question ${q.id}`}
                  className="w-full rounded-lg border border-slate-300 px-2 py-1 font-mono text-xs focus:border-indigo-400 focus:outline-none"
                />
                {edited !== q.formula && (
                  <div className="mt-1.5 rounded-lg border border-amber-200 bg-amber-50 p-2 text-amber-800">
                    <b>Re-lecture inverse automatique :</b> « {Q_READINGS[q.id]?.reading} » — cette lecture correspond
                    à la formule d'origine. Voulez-vous toujours dire <i>ceci</i> ?
                    <div className="mt-1 flex flex-wrap gap-2">
                      <button
                        onClick={() => setVariantQueued(true)}
                        className="rounded-lg border border-amber-400 bg-white px-2 py-0.5 text-[11px] font-medium hover:bg-amber-100"
                      >
                        oui — vérifier cette variante
                      </button>
                      <button
                        onClick={() => {
                          setEdited(q.formula);
                          setEditOpen(false);
                          setVariantQueued(false);
                        }}
                        className="text-[11px] underline hover:no-underline"
                      >
                        annuler
                      </button>
                    </div>
                    {variantQueued && (
                      <div className="mt-1 text-[11px] font-medium text-amber-900">
                        ✔ variante mise en file de vérification (asynchrone) — le verdict de l'outil restera le seul
                        juge ; la clause référencera ce run dans son dossier de preuve.
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {!answered && !coveredBy && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            onClick={() => start(q.clauseVolet, "impossible")}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
          >
            Impossible
          </button>
          <button
            onClick={answerNormal}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            C'est normal
          </button>
          <button
            onClick={() => start(null, "verify")}
            className="rounded-lg border border-indigo-300 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-50"
          >
            Vérifier d'abord
          </button>
        </div>
      )}

      {(phase === "queued" || phase === "running") && (
        <div className="mt-3">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-indigo-500" />
            {phaseLabel}
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div
              className={`h-full rounded-full bg-indigo-500 transition-all ${phase === "queued" ? "w-1/5" : "w-4/5"}`}
            />
          </div>
        </div>
      )}

      {phase === "done" && (
        <div className={`mt-3 rounded-xl border p-3 text-sm ${toneClasses[verdictTone]}`}>
          <div className="flex items-start gap-2">
            <span>{q.result === "impossible" ? "✔" : "⚠"}</span>
            <div>
              <span className="font-semibold">
                {q.result === "impossible" ? "Confirmé impossible." : "Refuté — le scénario est atteignable."}
              </span>{" "}
              {q.proof}
            </div>
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs opacity-80">
            <span className="rounded-md bg-white/70 px-2 py-0.5 font-medium">
              {q.kind === "static" ? "✔ vérifié par analyse statique" : "✔ vérifié par model checker"}
            </span>
            <span className="rounded-md bg-white/70 px-2 py-0.5 font-medium">
              {q.kind === "static"
                ? "contre-vérification : 2 solveurs d'invariants — concordants ✔"
                : "contre-vérification : LoLA + ITS-Tools — concordants ✔"}
            </span>
            {committed && (
              <span className="rounded-md bg-white/70 px-2 py-0.5 font-medium">
                {committed === "hypo" ? "classé : hypothèse d'environnement" : "clause ajoutée au contrat"}
              </span>
            )}
          </div>
        </div>
      )}

      {answered && phase !== "done" && committed === "hypo" && (
        <div className={`mt-3 rounded-xl border p-3 text-sm ${toneClasses.warn}`}>
          <span className="font-semibold">Classée hypothèse d'environnement</span> — « c'est normal » : l'information rejoint
          le volet hypothèses du contrat, sans vérification.
        </div>
      )}

      {phase === "done" && kind === "verify" && !committed && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            onClick={() => commitVolet(q.clauseVolet)}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
          >
            Ajouter au contrat
          </button>
          <button
            onClick={() => commitVolet("hypo")}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Hypothèse d'environnement
          </button>
        </div>
      )}

      {coveredBy && (
        <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50/60 p-3 text-sm text-violet-800">
          <b>≡ Convergence.</b> Question déjà arbitrée via votre proposition <b>{coveredBy}</b> — elle ne vous
          sollicitera plus ; la clause du contrat pointe vers le même dossier de preuve.
        </div>
      )}

      {answered && onRevise && !coveredBy && (committed !== null || (phase === null && kind === "normal")) && (
        <button
          onClick={() => onRevise(q.id)}
          className="mt-2 text-xs text-slate-400 hover:text-indigo-600 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
        >
          ↺ Réviser mon arbitrage — la décision précédente reste au journal horodaté
        </button>
      )}

      {phase === "done" && q.witness && q.result === "possible" && (
        <WitnessPlayer steps={q.witness.steps} cyclic={q.witness.cyclic} />
      )}
    </div>
  );
}

/* ============================== Proposition libre (boucle NL) ============================== */

function ProposalFlow({
  state,
  setState,
  onHumanCommit,
  onQueue,
  nextId,
  m3Answered,
}: {
  state: ProposalState;
  setState: (s: ProposalState) => void;
  onHumanCommit: (id: string, volet: Volet, text: string, cluster?: string, equiv?: string) => void;
  onQueue: (id: string, text: string, formula: string) => void;
  nextId: string;
  m3Answered?: boolean;
}) {
  const [input, setInput] = useState("");
  const scripted = useMemo(() => /faim|finira/i.test(state.text), [state.text]);

  useEffect(() => {
    if (state.stage !== "verify" || !scripted) return;
    if (state.phase === null) {
      const t = setTimeout(() => setState({ ...state, phase: "queued" }), 100);
      return () => clearTimeout(t);
    }
    if (state.phase === "queued") {
      const t = setTimeout(() => setState({ ...state, phase: "running" }), 800);
      return () => clearTimeout(t);
    }
    if (state.phase === "running") {
      const t = setTimeout(() => setState({ ...state, stage: "verdict" }), 1800);
      return () => clearTimeout(t);
    }
  }, [state.stage, state.phase]);

  const cat = useMemo(() => {
    const t = state.text;
    if (/jamais|interdit|ne doit pas|pas possib|impossible/i.test(t)) return "never";
    if (/toujours|finit|finira|doit pouvoir/i.test(t)) return "always";
    if (/peut|possib|atteign/i.test(t)) return "possible";
    return "unknown";
  }, [state.text]);

  const tokens = useMemo(() => {
    const STOP = new Set([
      "les", "des", "une", "dans", "pour", "avec", "sans", "par", "que", "qui", "pas", "toujours",
      "jamais", "devrait", "doivent", "peuvent", "avant", "après", "plus", "est", "sont", "elle",
    ]);
    return state.text
      .toLowerCase()
      .split(/[^a-zàâçéèêëîïôûùüÿñæœ]+/)
      .filter((w) => w.length >= 3 && !STOP.has(w))
      .slice(0, 4);
  }, [state.text]);

  const reset = () => setState({ stage: "input", text: "", option: null, phase: null });
  const close = () => setState({ stage: "closed", text: "", option: null, phase: null });

  if (state.stage === "closed") {
    return (
      <div className="rounded-2xl border border-indigo-200 bg-indigo-50/40 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-indigo-900">Une propriété à vous ?</div>
            <p className="text-xs text-indigo-700/80">
              Formulez-la en langage naturel — l'IA la reformule, la formalise en syntaxe MCC, la fait vérifier, puis
              l'ajoute au contrat.
            </p>
          </div>
          <button
            onClick={() => setState({ stage: "input", text: "", option: null, phase: null })}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
          >
            ＋ Ajouter ma propre propriété
          </button>
        </div>
      </div>
    );
  }

  const Bubble = ({ who, children }: { who: "ai" | "user"; children: React.ReactNode }) => (
    <div className={`flex ${who === "user" ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[88%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
          who === "user" ? "bg-slate-200 text-slate-800" : "border border-indigo-100 bg-indigo-50 text-slate-800"
        }`}
      >
        {who === "ai" && (
          <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-400">assistant</div>
        )}
        {children}
      </div>
    </div>
  );

  const shownId = state.id || nextId;

  return (
    <div className="rounded-2xl border border-indigo-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Votre proposition ({shownId}) — boucle de formalisation
        </span>
        <button onClick={close} className="text-xs text-slate-400 hover:text-slate-600">
          fermer
        </button>
      </div>
      <div className="space-y-2.5">
        {state.stage !== "input" && <Bubble who="user">{state.text}</Bubble>}

        {state.stage === "input" && (
          <div>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={2}
              placeholder="Ex. : « Un philosophe qui a faim finira toujours par manger »"
              className="w-full rounded-xl border border-slate-300 p-3 text-sm focus:border-indigo-400 focus:outline-none"
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                disabled={!input.trim()}
                onClick={() => setState({ stage: "clarify", text: input.trim(), option: null, phase: null })}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 hover:bg-indigo-700"
              >
                Proposer
              </button>
              <button
                onClick={() => setInput("Un philosophe qui a faim finira toujours par manger")}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100"
              >
                utiliser l'exemple guidé
              </button>
            </div>
          </div>
        )}

        {state.stage === "clarify" && (
          <Bubble who="ai">
            {scripted ? (
              <>
                Voici ma compréhension : « un philosophe qui veut manger finira par manger » — je prends le philosophe 1
                comme représentant (symétrie d'ordre 3 vérifiée). Une précision change tout le contrat : « toujours »,
                c'est…
              </>
            ) : cat === "never" ? (
              <>Je lis une <b>interdiction</b>. De quel type ?</>
            ) : cat === "always" ? (
              <>Je lis une <b>garantie</b>. Inconditionnelle, ou conditionnelle ?</>
            ) : cat === "possible" ? (
              <>Je lis une <b>question d'atteignabilité</b>. Vous demandez si…</>
            ) : (
              <>Je veux être sûr de comprendre avant de formaliser. Votre proposition est…</>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              {(scripted
                ? ["Dans toutes les exécutions, même adverses", "Sous un ordonnanceur équitable"]
                : cat === "never"
                  ? ["Un état interdit, à tout instant (invariant)", "Un enchaînement interdit (séquence, LTL)"]
                  : cat === "always"
                    ? ["Elle finit toujours par arriver (AG AF)", "Elle finit par arriver quand une condition est remplie (AG → AF)"]
                    : cat === "possible"
                      ? ["Atteignable depuis l'état initial (EF)", "Atteignable depuis n'importe quel état (AG EF)"]
                      : ["Une interdiction (ne doit jamais arriver)", "Une garantie (doit finir par arriver)", "Une question (peut arriver ?)"]
              ).map((o) => (
                <button
                  key={o}
                  onClick={() => setState({ ...state, option: o, stage: "formula" })}
                  className="rounded-full border border-indigo-300 bg-white px-3 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
                >
                  {o}
                </button>
              ))}
            </div>
          </Bubble>
        )}

        {state.stage === "formula" && (
          <Bubble who="ai">
            {scripted ? (
              <>
                Formalisation proposée :
                <div className="my-1.5 rounded-lg bg-slate-900 px-3 py-2 font-mono text-xs text-emerald-300">
                  AG AF (eat_1)
                </div>
                <ul className="text-xs text-slate-600">
                  <li>
                    — lecture : quelle que soit l'exécution et le point de départ, le philosophe 1 finira inévitablement
                    par manger — indéfiniment ;
                  </li>
                  <li>— non-vacuité : EF (eat_1), satisfaite (on ne vérifie pas une propriété muette) ;</li>
                  <li>— triage : model checking (14 états, 27 arcs) · risque 3/5 × coût 3/3 ;</li>
                  <li>— hypothèse choisie : « {state.option} » ;</li>
                  <li>
                    — convergence : la grille guidewords avait déjà proposé cette propriété (M3, mot-guid « Late ») —
                    votre formulation la confirme comme importante.
                  </li>
                </ul>
              </>
            ) : (
              <>
                Squelette de formalisation (à instancier) :
                <div className="my-1.5 rounded-lg bg-slate-900 px-3 py-2 font-mono text-xs text-emerald-300">
                  {cat === "never"
                    ? "AG ¬ ( ⟨état interdit⟩ )"
                    : cat === "possible"
                      ? "EF ( ⟨situation recherchée⟩ )"
                      : state.option && state.option.includes("quand")
                        ? "AG ( ⟨condition⟩ → AF ⟨garantie⟩ )"
                        : "AG AF ( ⟨garantie⟩ )"}
                </div>
                <div className="mt-1.5 text-xs text-slate-700">
                  <b>Lecture :</b>{" "}
                  {cat === "never"
                    ? "à aucun instant, sur aucune exécution, l'état interdit ne se produit."
                    : cat === "possible"
                      ? "il existe une exécution où la situation recherchée se produit — au moins une fois."
                      : state.option && state.option.includes("quand")
                        ? "chaque fois que la condition est remplie, la garantie finit par arriver — sur toutes les suites."
                        : "quelle que soit l'exécution, la garantie finit inévitablement par arriver."}
                </div>
                <div className="text-xs text-slate-600">
                  Termes à lier aux éléments du modèle :{" "}
                  {tokens.length ? tokens.map((t) => `« ${t} »`).join(", ") : "(aucun détecté)"} — je propose la
                  correspondance à la confirmation ; la vérification aura le dernier mot.
                </div>
              </>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                onClick={() => {
                  if (scripted) {
                    setState({ ...state, stage: "verify", phase: null, id: nextId });
                  } else {
                    const id = nextId;
                    onQueue(
                      id,
                      state.text,
                      cat === "never" ? "AG ¬( … )" : cat === "possible" ? "EF ( … )" : "AG ( … → AF … )"
                    );
                    setState({ ...state, stage: "queued", id });
                  }
                }}
                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
              >
                Confirmer et vérifier
              </button>
              <button
                onClick={reset}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
              >
                Reformuler
              </button>
            </div>
          </Bubble>
        )}

        {state.stage === "verify" && (
          <Bubble who="ai">
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-indigo-500" />
              {state.phase === "running"
                ? "Exploration d'états en cours — AG AF (eat_1)…"
                : "En file d'attente (budget 30 s)…"}
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full bg-indigo-500 transition-all ${state.phase === "running" ? "w-4/5" : "w-1/5"}`}
              />
            </div>
          </Bubble>
        )}

        {state.stage === "queued" && (
          <Bubble who="ai">
            Proposition <b>{shownId}</b> enregistrée et formalisée — en attente du batch de vérification planifié
            (asynchrone, budget 60 s). Le verdict sera notifié ; la proposition apparaît dans la file d'attente latérale.
            <div className="mt-2">
              <button
                onClick={reset}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
              >
                ＋ Nouvelle proposition
              </button>
            </div>
          </Bubble>
        )}

        {state.stage === "verdict" && (
          <Bubble who="ai">
            <div className={`rounded-xl border p-3 text-sm ${toneClasses.warn}`}>
              <b>Réfuté sans équité.</b> AG AF (eat_1) est faux : témoin ci-dessous — le philosophe 2 mange en boucle ;
              à chaque tour, fork_2 repasse un instant sur la table (takeR_1 activable) sans jamais être donné au
              philosophe 1. <b>Sous équité forte, la propriété devient vraie</b> : le défaut est une hypothèse manquante
              dans la spécification, pas un bug du modèle.
            </div>
            {m3Answered && (
              <div className="mt-2 rounded-lg border border-violet-200 bg-violet-50 p-2 text-xs text-violet-800">
                ≡ Convergence : la question <b>M3</b> pose la même propriété (mot-guid « Late ») et vous l'avez déjà
                arbitrée. Votre décision ici <b>fusionne</b> avec elle — M3 sera marquée couverte, sans nouvelle
                sollicitation.
              </div>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                onClick={() => {
                  onHumanCommit(
                    shownId,
                    "always",
                    "Un philosophe qui veut manger finit toujours par manger — même sous ordonnancement adverse",
                    "B",
                    "M3"
                  );
                  setState({ ...state, stage: "committed" });
                }}
                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
              >
                Ajouter au contrat (violation)
              </button>
              <button
                onClick={() => {
                  onHumanCommit(shownId, "hypo", "Ordonnanceur équitable (équité forte) — sinon famine possible", "B", "M3");
                  setState({ ...state, stage: "committed" });
                }}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
              >
                Classer comme hypothèse d'équité
              </button>
              <button
                onClick={reset}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
              >
                Reformuler
              </button>
            </div>
          </Bubble>
        )}
      </div>

      {state.stage === "verdict" && <WitnessPlayer steps={STARVATION_TRACE} cyclic />}

      {state.stage === "committed" && (
        <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          ✔ Proposition {shownId} intégrée au contrat — provenance : <b>langage naturel humain</b> × formalisation IA ×
          vérification outil (run archivé).
          <div className="mt-2">
            <button
              onClick={reset}
              className="rounded-lg border border-emerald-300 bg-white px-3 py-1.5 text-sm text-emerald-700 hover:bg-emerald-100"
            >
              ＋ Nouvelle proposition
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const Q_READINGS: Record<string, { reading: string; notSays: string }> = {
  S1: {
    reading:
      "À tout instant, la fourchette i est soit sur la table, soit tenue à gauche par son propriétaire, soit utilisée par lui (repas) ou par son voisin de droite — un seul détenteur à la fois.",
    notSays: "Rien sur qui mange ni quand — seulement l'unicité du détenteur.",
  },
  S2: {
    reading:
      "Le nombre total de jetons (philosophes et fourchettes) est constant : rien ne se crée, rien ne se perd dans le système fermé.",
    notSays: "Ne dit pas où sont les jetons — seulement combien.",
  },
  S3: {
    reading:
      "Chaque philosophe est exactement dans l'un de ses trois états — réfléchir, fourchette en main, manger — jamais deux à la fois.",
    notSays: "Rien sur la durée passée dans chaque état.",
  },
  S4: {
    reading:
      "Manger (eat_i) n'est atteignable qu'en passant par la prise de la fourchette droite — qui exige elle-même la gauche en main : l'enchaînement acquisition → repas est imposé.",
    notSays: "Ne dit pas que le repas a lieu — seulement le chemin pour y arriver.",
  },
  S5: {
    reading: "Impossible de prendre la fourchette droite sans tenir déjà la gauche : l'ordre d'acquisition est structurellement fixé.",
    notSays: "Rien sur l'ordre des philosophes entre eux.",
  },
  S6: {
    reading: "Une fourchette ne revient sur la table qu'à la fin du repas de son utilisateur — jamais avant, jamais par un tiers.",
    notSays: "Ne dit pas que le repas finit — seulement qui a le droit de la reposer.",
  },
  M1: {
    reading: "Il existe une exécution où plus aucune transition n'est activable : le système entier se fige. Une seule exécution suffit.",
    notSays: "Ne dit pas que l'impasse est inévitable — c'est AG EF DEADLOCK qui le dirait.",
  },
  M2: {
    reading:
      "Chaque fois que le philosophe 1 tient sa fourchette gauche, il est garanti que ce geste se résout : manger, ou reposer. Sur toutes les suites.",
    notSays: "Ne dit pas lequel des deux aboutissements.",
  },
  M3: {
    reading: "Quelle que soit l'exécution et le point de départ, le philosophe 1 finira inévitablement par manger — indéfiniment.",
    notSays: "Rien sur le délai ; fausse sans équité de l'ordonnanceur.",
  },
  M4: {
    reading: "Chaque fois que le philosophe 1 mange, il est garanti qu'il repassera à la réflexion — sur toutes les suites, sans hypothèse.",
    notSays: "Ne garantit pas qu'il mange un jour — seulement la sortie.",
  },
  M5: {
    reading:
      "Dans toute situation atteignable, il reste toujours un chemin pour remettre la fourchette 1 sur la table : la ressource n'est jamais perdue à jamais.",
    notSays: "Possible n'est pas certain : un adversaire peut repousser indéfiniment.",
  },
};

const READINGS: { formula: string; pattern: string; reading: string; notSays: string; cross: string }[] = [
  {
    formula: "EF (eat_1)",
    pattern: "atteignabilité",
    reading:
      "Il existe au moins une exécution où le philosophe 1 finit par être en train de manger. Autrement dit : manger est possible — au moins une fois, sur au moins un chemin.",
    notSays:
      "Rien sur les autres exécutions : une seule suffit. C'est la plus faible des lectures — et c'est précisément son rôle : elle sert de précondition de non-vacuité aux propriétés plus fortes.",
    cross: "non-vacuité de la question M3 — satisfaite",
  },
  {
    formula: "AG EF (fork_1 = 1)",
    pattern: "récurrence",
    reading:
      "Dans toute situation atteignable, il reste toujours un chemin pour remettre la fourchette 1 sur la table : quelle que soit la catastrophe intermédiaire, la ressource n'est pas perdue à jamais — on peut s'en remettre.",
    notSays:
      "Possible n'est pas certain : un adversaire peut repousser indéfiniment. Et « toute situation » inclut l'impasse, où plus aucun chemin n'existe — c'est exactement là qu'elle tombe.",
    cross: "question M5 — réfutée par l'impasse",
  },
  {
    formula: "AG AF (eat_1)",
    pattern: "vivacité",
    reading:
      "Quelle que soit l'exécution et le point de départ, le philosophe 1 finira inévitablement par manger — et l'histoire se répète : il remangera toujours, indéfiniment.",
    notSays:
      "Rien sur le délai. Et surtout : rien sans hypothèse d'ordonnancement — un adversaire peut le laisser mourir de faim en donnant toujours la fourchette aux autres.",
    cross: "question M3 / proposition humaine — fausse sans équité forte",
  },
  {
    formula: "AG (eat_1 → AF think_1)",
    pattern: "vivacité conditionnelle",
    reading:
      "Chaque fois que le philosophe 1 est en train de manger (section critique), il est garanti qu'il en sortira : le retour à la réflexion (think_1) finit par arriver, sur toutes les suites.",
    notSays:
      "Ne garantit ni qu'il mange souvent, ni même qu'il mange un jour — seulement la sortie, jamais l'entrée.",
    cross: "question M4 — vraie sans aucune hypothèse d'équité",
  },
  {
    formula: "AG (hasL_1 → AF (eat_1 ∨ think_1))",
    pattern: "vivacité de flux partiel",
    reading:
      "Chaque fois que le philosophe 1 tient sa fourchette gauche, il est garanti que ce geste commencé se résout : soit il mangera, soit il reposera sa fourchette. Un flux partiel finit toujours — d'une manière ou d'une autre.",
    notSays:
      "Ne dit pas lequel des deux aboutissements. Et elle tombe précisément dans l'impasse, où le geste est figé pour toujours.",
    cross: "question M2 — réfutée (même témoin que l'impasse)",
  },
  {
    formula: "EF DEADLOCK",
    pattern: "atteignabilité d'impasse",
    reading:
      "Il existe une exécution qui conduit à un marquage où plus aucune transition n'est activable : le système entier se fige. Ici : les trois philosophes saisissent chacun leur fourchette gauche et se regardent.",
    notSays:
      "Ne dit pas que l'impasse est inévitable — une seule exécution suffit. C'est AG EF DEADLOCK qui exprimerait qu'on peut toujours y retomber.",
    cross: "question M1 — atteinte en 3 tirs",
  },
  {
    formula: "AG ¬(eat_1 ∧ eat_2)",
    pattern: "exclusion mutuelle (sûreté)",
    reading:
      "À aucun instant, sur aucune exécution, les philosophes 1 et 2 ne mangent en même temps : la fourchette 2 qu'ils partagent impose l'exclusion mutuelle.",
    notSays:
      "Rien sur les non-voisins (dans un anneau de 3, il n'y en a pas), rien sur « quelqu'un mange » — seulement « jamais ces deux-là ensemble ».",
    cross: "patron de la question S1 — prouvable par invariant statique",
  },
];

function ReverseReader() {
  const [sel, setSel] = useState(READINGS[2].formula);
  const r = READINGS.find((x) => x.formula === sel) || READINGS[2];
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-sm font-semibold text-slate-800">Traduction inverse — lire une formule avant de la croire</div>
      <p className="mt-1 text-xs leading-relaxed text-slate-500">
        Chaque formule du catalogue peut être relue en langage naturel, éléments nommés par leurs rôles sémantiques.
        La traduction fait partie de l'audit : elle dit ce que la formule dit — pas ce qu'on croit qu'elle dit.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {READINGS.map((x) => (
          <button
            key={x.formula}
            onClick={() => setSel(x.formula)}
            className={`rounded-lg px-2.5 py-1 font-mono text-xs ${
              x.formula === sel ? "bg-indigo-600 text-white" : "border border-slate-300 text-slate-600 hover:bg-slate-100"
            }`}
          >
            {x.formula}
          </button>
        ))}
      </div>
      <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
        <span className="rounded-md bg-indigo-100 px-2 py-0.5 text-xs font-semibold text-indigo-700">{r.pattern}</span>
        <p className="mt-2 text-sm leading-relaxed text-slate-800">{r.reading}</p>
        <div className="mt-2 rounded-lg border border-slate-200 bg-white p-2 text-xs leading-relaxed text-slate-600">
          <b>Ce qu'elle ne dit pas :</b> {r.notSays}
        </div>
        <div className="mt-2 text-xs font-medium text-indigo-600">↔ {r.cross}</div>
      </div>
      <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/50 p-3 text-xs leading-relaxed text-slate-700">
        <b>Les trois sosies</b> — même prédicat (eat_1), trois contrats différents, souvent confondus :
        <ul className="mt-1 list-disc pl-4">
          <li>
            <span className="font-mono">EF (eat_1)</span> — possible au moins une fois (la plus faible) ;
          </li>
          <li>
            <span className="font-mono">AG EF (eat_1)</span> — toujours possible de s'y remettre (récurrence) ;
          </li>
          <li>
            <span className="font-mono">AG AF (eat_1)</span> — inévitable, à chaque fois (la plus forte) — exige
            l'équité.
          </li>
        </ul>
      </div>
    </div>
  );
}

/* ============================== Amorçage ============================== */

function AmorceStep({ onValidate }: { onValidate: () => void }) {
  const [corrections, setCorrections] = useState<Record<string, string>>({});
  const [focus, setFocus] = useState<string | null>(null);
  const nbWrong = AMORCE.filter((a) => a.wrong && !corrections[a.id]).length;
  const focused = AMORCE.find((a) => a.id === focus);
  return (
    <div>
      <p className="mb-4 text-sm leading-relaxed text-slate-600">
        L'IA présente le modèle <span className="font-medium">tel qu'elle l'a compris</span>. Cliquez une place du
        modèle pour interroger ou corriger son rôle — la sémantique se discute sur le graphe, pas dans une liste.
        Chaque correction est traçée comme une réponse humaine. Les rôles guident la génération de propriétés à
        l'étape suivante.
      </p>
      <div className="mb-4 rounded-2xl border border-slate-200 bg-slate-50/60 p-3">
        <NetView
          active={[]}
          gained={[]}
          onPlaceClick={(g) => setFocus(g === focus ? null : g)}
          selectedGroup={focus}
        />
        {focused && (
          <div className="mt-2 rounded-xl border border-indigo-200 bg-white p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-slate-700">{focused.elements}</span>
              <span
                className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                  corrections[focused.id] ? "bg-violet-100 text-violet-700" : "bg-slate-100 text-slate-700"
                }`}
              >
                {corrections[focused.id] || focused.role}
              </span>
              <span className="text-xs text-slate-400">confiance {focused.conf}</span>
              {focused.wrong && !corrections[focused.id] && (
                <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                  à confirmer
                </span>
              )}
            </div>
            <div className="mt-1 text-xs italic text-slate-500">preuve : {focused.evidence}</div>
            {focused.wrong ? (
              !corrections[focused.id] && (
                <select
                  className="mt-2 rounded-lg border border-amber-400 bg-white px-2 py-1 text-xs"
                  value=""
                  aria-label={`Corriger le rôle de ${focused.elements}`}
                  onChange={(e) => {
                    if (e.target.value) setCorrections((c) => ({ ...c, [focused.id]: e.target.value }));
                  }}
                >
                  <option value="">corriger ce rôle…</option>
                  {(focused.options || []).map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              )
            ) : (
              <div className="mt-1 text-xs text-emerald-600">
                rôle confirmé — cliquer une autre place pour l'interroger
              </div>
            )}
          </div>
        )}
      </div>
      <div className="space-y-2">
        {AMORCE.map((a) => {
          const corrected = corrections[a.id];
          const suspicious = a.wrong && !corrected;
          return (
            <div
              key={a.id}
              className={`rounded-xl border p-3 ${suspicious ? "border-amber-300 bg-amber-50/40" : "border-slate-200 bg-white"}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-700">{a.elements}</span>
                <span
                  className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                    corrected ? "bg-violet-100 text-violet-700" : "bg-slate-100 text-slate-700"
                  }`}
                >
                  {corrected || a.role}
                </span>
                {corrected ? (
                  <span className="rounded-md bg-violet-600 px-2 py-0.5 text-xs font-medium text-white">
                    corrigé par l'humain
                  </span>
                ) : (
                  <span className="text-xs text-slate-400">confiance {a.conf}</span>
                )}
                {suspicious && (
                  <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                    à confirmer
                  </span>
                )}
              </div>
              <div className="mt-1 text-xs italic text-slate-500">preuve : {a.evidence}</div>
              {a.wrong && !corrected && (
                <div className="mt-2">
                  <button
                    onClick={() => setFocus(a.id)}
                    className="rounded-lg border border-amber-400 bg-white px-3 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100"
                  >
                    {focus === a.id ? "corriger sur le modèle ▲" : "discuter ce rôle sur le modèle"}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={onValidate}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
        >
          Valider l'amorçage
        </button>
        {nbWrong > 0 && (
          <span className="text-xs text-amber-700">
            {nbWrong} extraction{nbWrong > 1 ? "s" : ""} marquée{nbWrong > 1 ? "s" : ""} « à confirmer » — corriger
            améliore la génération de propriétés.
          </span>
        )}
      </div>
    </div>
  );
}

/* ============================== Synthèse ============================== */

function SyntheseStep({ clauses, answered, coverage }: { clauses: Clause[]; answered: number; coverage: number }) {
  const [copied, setCopied] = useState(false);
  const [showMd, setShowMd] = useState(false);
  const [openDossier, setOpenDossier] = useState<string | null>(null);
  const provedNever = clauses.filter((c) => c.volet === "never" && c.status === "proved");
  const provedAlways = clauses.filter((c) => c.volet === "always" && c.status === "proved");
  const hypos = clauses.filter((c) => c.volet === "hypo");
  const violations = clauses.filter((c) => c.status === "refuted");
  const staticCount = STATIC_QS.filter((q) => clauses.some((c) => c.qid === q.id)).length;
  const mcCount = MC_QS.filter((q) => clauses.some((c) => c.qid === q.id)).length;
  const groups = ["A", "B"].filter((g) => violations.some((v) => v.cluster === g));

  const buildMd = () => {
    const li = (c: Clause) =>
      `- [${c.qid}] ${c.text} — ${
        c.status === "proved" ? "prouvée" : c.status === "refuted" ? "RÉFUTÉE (violation)" : "hypothèse"
      } · ${c.tool}${c.cost ? ` · ${c.cost}` : ""}`;
    let md = `# Contrat — Philosophes-3\n\n`;
    md += `## Ne doit jamais arriver\n${provedNever.length ? provedNever.map(li).join("\n") : "- (aucune)"}\n\n`;
    md += `## Doit arriver\n${provedAlways.length ? provedAlways.map(li).join("\n") : "- (aucune)"}\n\n`;
    md += `## Hypothèses explicites\n${hypos.length ? hypos.map(li).join("\n") : "- (aucune)"}\n\n`;
    md += `## Violations (${violations.length}) → causes racines\n`;
    groups.forEach((g) => {
      const meta = CLUSTERS[g];
      md += `- **${meta.cause} — ${meta.title}** : ${violations.filter((v) => v.cluster === g).map((v) => v.qid).join(", ")}\n`;
    });
    if (!groups.length) md += "- (aucune)\n";
    return md;
  };

  const Volet = ({ title, tone, items, empty }: { title: string; tone: string; items: Clause[]; empty: string }) => (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className={`inline-block rounded-md px-2 py-0.5 text-xs font-semibold ${tone}`}>{title}</div>
      {items.length === 0 ? (
        <p className="mt-3 text-xs italic text-slate-400">{empty}</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((c) => (
            <li key={c.qid} className="text-sm leading-snug text-slate-700">
              <button
                onClick={() => setOpenDossier(openDossier === c.qid ? null : c.qid)}
                className="w-full text-left hover:text-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
                aria-expanded={openDossier === c.qid}
              >
                <span className="mr-1 font-mono text-xs text-slate-400">{c.qid}</span>
                {c.text}
              </button>
              <div className="mt-0.5 text-[11px] text-slate-400">
                via {c.tool} — {c.source} ·{" "}
                <span className="text-indigo-500">{openDossier === c.qid ? "masquer le dossier" : "dossier de preuve ▾"}</span>
              </div>
              {openDossier === c.qid && (
                <div className="mt-1 rounded-lg border border-slate-200 bg-slate-50 p-2 text-[11px] leading-relaxed text-slate-600">
                  <div className="font-mono text-slate-800">{c.formula || "—"}</div>
                  <div className="mt-1">
                    <b>Run :</b> {c.runs || "—"}
                  </div>
                  <div>
                    <b>Coût :</b> {c.cost || "—"}
                  </div>
                  <div>
                    <b>Statut :</b>{" "}
                    {c.status === "proved"
                      ? "prouvée par outil"
                      : c.status === "refuted"
                        ? "réfutée (violation)"
                        : "hypothèse — non vérifiée, arbitrée par l'humain"}
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );

  return (
    <div>
      <div className="mb-4 grid grid-cols-4 gap-3">
        {[
          { v: answered, l: "questions arbitrées" },
          { v: staticCount, l: "tranchées statiquement (gratuit)" },
          { v: mcCount, l: "par model checking" },
          { v: coverage + "%", l: "couverture du modèle" },
        ].map((s) => (
          <div key={s.l} className="rounded-2xl border border-slate-200 bg-white p-3 text-center shadow-sm">
            <div className="text-2xl font-bold text-indigo-600">{s.v}</div>
            <div className="text-[11px] leading-tight text-slate-500">{s.l}</div>
          </div>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button
          onClick={async () => {
            const md = buildMd();
            try {
              await navigator.clipboard.writeText(md);
              setCopied(true);
              setTimeout(() => setCopied(false), 2500);
            } catch {
              setShowMd(true);
            }
          }}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
        >
          {copied ? "✔ contrat copié (Markdown)" : "⧉ Copier le contrat (Markdown)"}
        </button>
        <button
          onClick={() => setShowMd(!showMd)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
        >
          {showMd ? "masquer" : "voir"} le Markdown
        </button>
        <span className="text-xs text-slate-400">
          le contrat est le livrable — il quitte l'outil avec sa traçabilité ; cliquez une clause pour son dossier de preuve
        </span>
      </div>
      {showMd && (
        <textarea
          readOnly
          value={buildMd()}
          rows={10}
          aria-label="Contrat au format Markdown"
          className="mb-4 w-full rounded-xl border border-slate-300 p-3 font-mono text-[11px] focus:outline-none"
        />
      )}

      {groups.length > 0 && (
        <div className="mb-4 rounded-2xl border-2 border-rose-200 bg-rose-50/50 p-4">
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-rose-600 px-2 py-0.5 text-xs font-semibold text-white">Diagnostic</span>
            <span className="text-sm text-slate-700">
              {violations.length} violation{violations.length > 1 ? "s" : ""} → {groups.length} cause
              {groups.length > 1 ? "s" : ""} racine{groups.length > 1 ? "s" : ""} — un ingénieur voyant «{" "}
              {violations.length} échecs » conclurait « {violations.length} bugs ».
            </span>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {groups.map((g) => {
              const meta = CLUSTERS[g];
              const members = violations.filter((v) => v.cluster === g);
              return (
                <div key={g} className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
                  <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${meta.tone}`}>{meta.cause}</span>
                  <div className="mt-2 text-sm font-semibold text-slate-800">{meta.title}</div>
                  <ul className="mt-2 space-y-1">
                    {members.map((m) => (
                      <li key={m.qid} className="text-xs text-slate-600">
                        <span className="font-mono text-slate-400">{m.qid}</span> « {m.text} » — réfutée
                      </li>
                    ))}
                  </ul>
                  <div className="mt-2 text-xs italic text-slate-500">{meta.note}</div>
                  <div className="mt-2 rounded-lg bg-slate-50 p-2 text-xs text-slate-700">
                    <span className="font-semibold">Remédiation : </span>
                    {meta.fix}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Volet
          title="NE DOIT JAMAIS ARRIVER"
          tone="bg-rose-100 text-rose-700"
          items={provedNever}
          empty="aucune clause — répondez « Impossible » aux questions."
        />
        <Volet
          title="DOIT ARRIVER"
          tone="bg-emerald-100 text-emerald-700"
          items={provedAlways}
          empty="aucune clause — ex. la sortie de section critique (M4)."
        />
        <Volet
          title="HYPOTHÈSES EXPLICITES"
          tone="bg-amber-100 text-amber-700"
          items={hypos}
          empty="aucune — ex. l'équité forte, si la famine est jugée « normale »."
        />
      </div>

      <p className="mt-4 text-xs leading-relaxed text-slate-500">
        Chaque clause est traçable de bout en bout : générée par un patron guideword × rôle sémantique, arbitrée par
        l'humain (impossible / normal / vérifier), tranchée par un outil formel dont le run et le coût sont archivés.
        L'IA n'a jamais tranché seule ; elle n'a jamais non plus été crue sur parole.
      </p>
    </div>
  );
}

/* ============================== Jauge ============================== */

function Gauge({ pct }: { pct: number }) {
  const C = 2 * Math.PI * 34;
  return (
    <svg width="88" height="88" viewBox="0 0 88 88">
      <circle cx="44" cy="44" r="34" fill="none" stroke="#e2e8f0" strokeWidth="10" />
      <circle
        cx="44"
        cy="44"
        r="34"
        fill="none"
        stroke="#6366f1"
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={`${(pct / 100) * C} ${C}`}
        transform="rotate(-90 44 44)"
      />
      <text x="44" y="49" textAnchor="middle" fontSize="17" fontWeight="700" fill="#334155">
        {pct}%
      </text>
    </svg>
  );
}

/* ============================== App ============================== */

const STEPS = ["Amorçage", "Questions statiques", "Vérifications MC", "Synthèse"];

export default function App() {
  const [step, setStep] = useState(0);
  const [amorced, setAmorced] = useState(false);
  const [answeredMap, setAnsweredMap] = useState<Record<string, string>>({});
  const [clauses, setClauses] = useState<Clause[]>([]);
  const [proposal, setProposal] = useState<ProposalState>({ stage: "closed", text: "", option: null, phase: null });
  const [pendingProps, setPendingProps] = useState<{ id: string; text: string; formula: string }[]>([]);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [dedup, setDedup] = useState<Record<string, string>>({});
  const [spent, setSpent] = useState(0);
  const [hoverCover, setHoverCover] = useState<string[] | null>(null);
  const [saved, setSaved] = useState<Record<string, any> | null>(null);
  const [resumeShown, setResumeShown] = useState(false);
  const [persist, setPersist] = useState(false);

  const STORAGE_KEY = "elicitation-philosophes";

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const d = JSON.parse(raw);
        if (d && ((d.answeredMap && Object.keys(d.answeredMap).length) || (d.clauses && d.clauses.length) || (d.log && d.log.length))) {
          setSaved(d);
          setResumeShown(true);
        } else {
          setPersist(true);
        }
      } else {
        setPersist(true);
      }
    } catch {
      setPersist(true);
    }
  }, []);

  useEffect(() => {
    if (!persist || resumeShown) return;
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ step, amorced, answeredMap, clauses, log, dedup, spent, pendingProps })
      );
    } catch {}
  }, [step, amorced, answeredMap, clauses, log, dedup, spent, pendingProps, persist, resumeShown]);

  const pushLog = (label: string, tone: LogEntry["tone"]) =>
    setLog((l) => [...l, { ts: new Date().toLocaleTimeString("fr-FR"), label, tone }]);

  const doResume = () => {
    if (saved) {
      setStep(typeof saved.step === "number" ? saved.step : 0);
      setAmorced(!!saved.amorced);
      setAnsweredMap(saved.answeredMap || {});
      setClauses(saved.clauses || []);
      setLog(saved.log || []);
      setDedup(saved.dedup || {});
      setSpent(saved.spent || 0);
      setPendingProps(saved.pendingProps || []);
    }
    setPersist(true);
    setResumeShown(false);
  };

  const startFresh = () => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {}
    setPersist(true);
    setResumeShown(false);
  };

  const onAnswer = (id: string, kind: string) => {
    setAnsweredMap((m) => ({ ...m, [id]: kind }));
    const q = QUESTIONS.find((x) => x.id === id);
    if (!q) return;
    if (q.kind === "mc" && (kind === "impossible" || kind === "verify")) {
      setSpent((s) => s + q.cost);
      pushLog(`${id} — run MC terminé (${q.cost} s), verdict rendu`, "answer");
    } else if (kind === "verify") {
      pushLog(`${id} — vérifiée à la demande (statique, gratuit)`, "answer");
    } else if (kind === "normal") {
      pushLog(`${id} — arbitrée « c'est normal » → hypothèse d'environnement`, "answer");
    } else {
      pushLog(`${id} — arbitrée « impossible », intuition confirmée par outil`, "answer");
    }
  };

  const onCommit = (q: Question, volet: Volet) => {
    let text = "";
    let status: ClauseStatus = "hypothesis";
    if (volet === "hypo") {
      text = q.normalText;
    } else if (volet === "never") {
      text = q.neverText || "";
      status = q.result === "impossible" ? "proved" : "refuted";
    } else {
      text = q.alwaysText || "";
      status = q.result === "impossible" ? "proved" : "refuted";
    }
    const clause: Clause = {
      qid: q.id,
      volet,
      text,
      status,
      tool: q.kind === "static" ? "analyse statique (algèbre)" : "model checker (exploration)",
      source: `guideword « ${q.guideword} » × réponse humaine`,
      cluster: q.cluster,
      formula: q.formula,
      runs: q.proof,
      cost: q.kind === "static" ? "0 s — tranchée statiquement (gratuit)" : `${q.cost} s d'exploration — run archivé`,
    };
    setClauses((cs) => [...cs.filter((c) => c.qid !== q.id), clause]);
    pushLog(
      `${q.id} — ${
        volet === "hypo" ? "hypothèse d'environnement" : volet === "never" ? "clause « ne jamais »" : "clause « toujours »"
      }${status === "refuted" ? " (VIOLATION)" : " (prouvée)"}`,
      "commit"
    );
  };

  const onHumanCommit = (id: string, volet: Volet, text: string, cluster?: string, equiv?: string) => {
    const clause: Clause = {
      qid: id,
      volet,
      text,
      status: volet === "hypo" ? "hypothesis" : "refuted",
      tool: "model checker (exploration)",
      source: "proposition humaine (langage naturel) × formalisation IA",
      cluster,
      formula: "AG AF (eat_1)",
      runs:
        "Réfutée sans équité : cycle du philosophe 2 seul ; takeR_1 activable 1×/tour, jamais choisie (14 états, 27 arcs).",
      cost: "3 s d'exploration — run archivé",
    };
    setClauses((cs) => [...cs.filter((c) => c.qid !== id), clause]);
    setSpent((s) => s + 3);
    pushLog(`${id} — proposition humaine arbitrée (${volet === "hypo" ? "hypothèse d'équité" : "violation"})`, "proposal");
    if (equiv) {
      setDedup((d) => ({ ...d, [equiv]: id }));
      setAnsweredMap((m) => (m[equiv] === undefined ? { ...m, [equiv]: "covered" } : m));
      pushLog(`${equiv} ≡ ${id} — fusion par convergence, ${equiv} marquée couverte`, "proposal");
    }
  };

  const onQueue = (id: string, text: string, formula: string) => {
    setPendingProps((p) => [...p, { id, text, formula }]);
    pushLog(`${id} — proposition enregistrée, en file de vérification asynchrone`, "proposal");
  };

  const onRevise = (id: string) => {
    setAnsweredMap((m) => {
      const c = { ...m };
      delete c[id];
      return c;
    });
    setClauses((cs) => cs.filter((c) => c.qid !== id));
    pushLog(`${id} — arbitrage rouvert pour révision (clause retirée ; décision conservée au journal)`, "revise");
  };

  const answeredIds = Object.keys(answeredMap);
  const coveredSet = useMemo(() => {
    const set = new Set<string>();
    QUESTIONS.filter((q) => answeredIds.includes(q.id) || !!dedup[q.id]).forEach((q) =>
      q.cover.forEach((c) => set.add(c))
    );
    return set;
  }, [answeredIds, dedup]);
  const coverage = Math.round((coveredSet.size / COVERAGE_GROUPS.length) * 100);

  const never = clauses.filter((c) => c.volet === "never" && c.status === "proved").length;
  const always = clauses.filter((c) => c.volet === "always" && c.status === "proved").length;
  const hypos = clauses.filter((c) => c.volet === "hypo").length;
  const refuted = clauses.filter((c) => c.status === "refuted").length;

  return (
    <div className="min-h-screen bg-slate-100 p-4 font-sans text-slate-800 md:p-6">
      <div className="mx-auto max-w-6xl">
        <header className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
          <div>
            <h1 className="text-lg font-bold">Session d'élicitation — Philosophes-3</h1>
            <p className="text-xs text-slate-500">
              Mode reconnaissance : l'IA montre, l'ingénieur arbitre, les outils tranchent.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded-md bg-slate-100 px-2 py-1 font-mono text-slate-500">clé api : demo-01</span>
            <span className="rounded-md bg-violet-100 px-2 py-1 font-medium text-violet-700">maquette interactive</span>
          </div>
        </header>

        {resumeShown && saved && (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-5 py-3 shadow-sm">
            <div className="text-sm text-amber-900">
              <b>Session précédente détectée</b> — {Object.keys(saved.answeredMap || {}).length} arbitrage(s),{" "}
              {(saved.clauses || []).length} clause(s), {saved.spent || 0} s de budget MC consommés. L'élicitation est
              un processus long : reprenez où vous vous êtes arrêté.
            </div>
            <div className="flex gap-2">
              <button
                onClick={doResume}
                className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-400"
              >
                Reprendre
              </button>
              <button
                onClick={startFresh}
                className="rounded-lg border border-amber-400 bg-white px-3 py-1.5 text-sm text-amber-800 hover:bg-amber-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-400"
              >
                Commencer à zéro
              </button>
            </div>
          </div>
        )}

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {STEPS.map((s, i) => {
            const unlocked = i === 0 || amorced;
            const active = step === i;
            return (
              <button
                key={s}
                disabled={!unlocked}
                onClick={() => setStep(i)}
                className={`rounded-full px-3 py-1.5 text-sm font-medium ${
                  active
                    ? "bg-indigo-600 text-white"
                    : unlocked
                      ? "border border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                      : "border border-slate-200 bg-slate-50 text-slate-300"
                }`}
              >
                {i + 1}. {s}
              </button>
            );
          })}
        </div>

        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <main className="min-h-[400px] rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            {step === 0 && <AmorceStep onValidate={() => { setAmorced(true); setStep(1); }} />}
            {step === 1 && (
              <div>
                <p className="mb-4 text-sm leading-relaxed text-slate-600">
                  Scénarios générés par la grille <span className="font-medium">rôles × mots-guides</span>, tranchables
                  par analyse statique : verdict immédiat, gratuit, sans exploration d'états. Répondez « Impossible »,
                  « C'est normal », ou vérifiez avant de croire.
                </p>
                <ProposalFlow
                  state={proposal}
                  setState={setProposal}
                  onHumanCommit={onHumanCommit}
                  onQueue={onQueue}
                  m3Answered={answeredMap["M3"] !== undefined}
                  nextId={"H" + (clauses.filter((c) => c.qid.startsWith("H")).length + pendingProps.length + 1)}
                />
                <div className="space-y-3">
                  {STATIC_QS.map((q) => (
                    <QuestionCard
                      key={q.id}
                      q={q}
                      answeredKind={answeredMap[q.id]}
                      coveredBy={dedup[q.id]}
                      onAnswer={onAnswer}
                      onCommit={onCommit}
                      onRevise={onRevise}
                      onHover={setHoverCover}
                    />
                  ))}
                </div>
                <div className="mt-4">
                  <ReverseReader />
                </div>
                <button
                  onClick={() => setStep(2)}
                  className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
                >
                  Continuer vers les vérifications MC ›
                </button>
              </div>
            )}
            {step === 2 && (
              <div>
                <p className="mb-4 text-sm leading-relaxed text-slate-600">
                  Propriétés nécessitant une exploration d'états, triées par <span className="font-medium">priorité
                  (risque × coût)</span> sous budget. Lancez, suivez le polling, puis lisez le verdict et son témoin —
                  joué pas à pas sur le modèle.
                </p>
                <ProposalFlow
                  state={proposal}
                  setState={setProposal}
                  onHumanCommit={onHumanCommit}
                  onQueue={onQueue}
                  m3Answered={answeredMap["M3"] !== undefined}
                  nextId={"H" + (clauses.filter((c) => c.qid.startsWith("H")).length + pendingProps.length + 1)}
                />
                <div className="space-y-3">
                  {MC_QS.map((q, i) => (
                    <QuestionCard
                      key={q.id}
                      q={q}
                      priority={i + 1}
                      answeredKind={answeredMap[q.id]}
                      coveredBy={dedup[q.id]}
                      onAnswer={onAnswer}
                      onCommit={onCommit}
                      onRevise={onRevise}
                      onHover={setHoverCover}
                    />
                  ))}
                </div>
                <div className="mt-4">
                  <ReverseReader />
                </div>
                <button
                  onClick={() => setStep(3)}
                  className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
                >
                  Voir la synthèse ›
                </button>
              </div>
            )}
            {step === 3 && <SyntheseStep clauses={clauses} answered={answeredIds.length} coverage={coverage} />}
          </main>

          <aside className="space-y-4">
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Modèle & couverture</div>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-400">read-only</span>
              </div>
              <div className="mt-1 text-xs text-slate-500">Philosophes-3 · P/T · 12 places · 9 transitions · PNML</div>
              <div className="mt-2">
                <NetView active={[]} gained={[]} covered={coveredSet} hover={hoverCover} />
              </div>
              <div className="mt-2 flex flex-wrap gap-1 text-[10px]">
                {COVERAGE_GROUPS.map((g) => {
                  const ok = coveredSet.has(g);
                  return (
                    <span
                      key={g}
                      className={`rounded px-1.5 py-0.5 font-mono ${ok ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-400"}`}
                    >
                      {ok ? "✔" : "✗"} {g}
                    </span>
                  );
                })}
              </div>
              <div className="mt-1 text-[10px] leading-tight text-slate-400">
                survolez une question : ses éléments s'allument · les éléments non contraints restent grisés
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4 text-center shadow-sm">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Couverture du modèle</div>
              <div className="mt-2 flex justify-center">
                <Gauge pct={coverage} />
              </div>
              <div className="mt-1 text-[11px] leading-tight text-slate-500">
                part des groupes d'éléments contraints par au moins une propriété arbitrée
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Budget MC cumulé</div>
              <div className="mt-2 flex items-baseline justify-between text-sm">
                <span className="font-semibold text-indigo-600">{spent} s</span>
                <span className="text-xs text-slate-400">/ 30 s alloués</span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-valuenow={spent} aria-valuemin={0} aria-valuemax={30} aria-label="Budget MC consommé">
                <div
                  className="h-full rounded-full bg-indigo-500 transition-all"
                  style={{ width: `${Math.min(100, (spent / 30) * 100)}%` }}
                />
              </div>
              <div className="mt-1 text-[10px] leading-tight text-slate-400">
                chaque run d'exploration est facturé ; réviser un arbitrage relance un run (réflexe MCC) — les questions
                statiques restent gratuites
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Contrat en cours</div>
              <div className="mt-2 space-y-1.5 text-sm">
                <div className="flex justify-between">
                  <span className="text-rose-600">ne doit jamais arriver</span>
                  <span className="font-semibold">{never}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-emerald-600">doit arriver</span>
                  <span className="font-semibold">{always}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-amber-600">hypothèses</span>
                  <span className="font-semibold">{hypos}</span>
                </div>
                <div className="flex justify-between border-t border-slate-100 pt-1.5">
                  <span className="text-rose-400">violations détectées</span>
                  <span className="font-semibold text-rose-500">{refuted}</span>
                </div>
                <div className="flex justify-between border-t border-slate-100 pt-1.5">
                  <span className="text-indigo-400">vos propositions en attente</span>
                  <span className="font-semibold">{pendingProps.length}</span>
                </div>
              </div>
            </div>

            {pendingProps.length > 0 && (
              <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  File d'attente — propositions humaines
                </div>
                <ul className="mt-2 space-y-1.5 text-xs text-slate-600">
                  {pendingProps.map((p) => (
                    <li key={p.id}>
                      <span className="font-mono text-slate-400">{p.id}</span> «
                      {p.text.length > 60 ? p.text.slice(0, 60) + "…" : p.text} »{" "}
                      <span className="font-mono text-[10px] text-indigo-500">{p.formula}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Journal horodaté des arbitrages
              </div>
              {log.length === 0 ? (
                <p className="mt-2 text-xs italic text-slate-400">
                  aucune décision pour l'instant — chaque arbitrage, révision et run sera conservé ici (exigence
                  d'audit : qui a décidé quoi, quand, sur quel verdict d'outil)
                </p>
              ) : (
                <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto pr-1 text-[11px] leading-tight">
                  {[...log].reverse().map((e, i) => (
                    <li key={log.length - i} className="flex gap-1.5">
                      <span className="font-mono text-slate-400">{e.ts}</span>
                      <span
                        className={
                          e.tone === "commit"
                            ? "text-slate-600"
                            : e.tone === "proposal"
                              ? "text-violet-600"
                              : e.tone === "revise"
                                ? "text-amber-600"
                                : "text-slate-500"
                        }
                      >
                        {e.label}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4 text-xs leading-relaxed text-slate-500 shadow-sm">
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Légende provenance</div>
              <div className="mb-1">
                <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700">
                  ✔ vérifié par outil
                </span>{" "}
                — verdict certifié (statique ou MC).
              </div>
              <div>
                <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[11px] font-medium text-violet-700">
                  hypothèse sémantique
                </span>{" "}
                — interprétation de l'IA, avec preuve, jamais un verdict.
              </div>
            </div>
          </aside>
        </div>

        <footer className="mt-4 rounded-2xl border border-slate-200 bg-white px-5 py-3 text-[11px] leading-relaxed text-slate-400 shadow-sm">
          Maquette de l'écran « session d'élicitation » (module M6) — données réelles issues de l'analyse du modèle
          Philosophes-3 menée dans la discussion (invariants vérifiés, 14 états / 27 arcs, traces d'impasse et de
          famine calculées) ; latences d'outils simulées. La boucle de formalisation des propositions humaines est
          scriptée : l'exemple guidé joue le dialogue complet (reformulation, clarification, formalisation AG AF
          (eat_1), vérification, verdict et intégration au contrat) ; toute autre saisie produit un squelette
          formalisé et part en file d'attente de vérification. L'UI ne contient aucune logique de décision : elle rend
          visible le contrat « l'IA propose, les outils disposent ». Arbitrages révisables avec journal horodaté,
          convergence question ↔ proposition (fusion sans double sollicitation), export Markdown du contrat et
          dossiers de preuve par clause, couverture projetée sur le modèle, budget MC cumulé, session reprenable
          localement.
        </footer>
      </div>
    </div>
  );
}