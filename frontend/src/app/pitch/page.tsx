import {
  ArrowRight,
  BadgeCheck,
  BrainCircuit,
  Building2,
  CheckCircle2,
  CircleDollarSign,
  Database,
  FileSpreadsheet,
  Gauge,
  GitBranch,
  Layers3,
  LockKeyhole,
  Network,
  Route,
  Search,
  ShieldCheck,
  Sparkles,
  Table2,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

const providerRoutes = [
  {
    name: "Parallel",
    role: "Cited enrichment",
    fit: "Structured rows, processor depth, source basis",
    tone: "forest",
  },
  {
    name: "Brave",
    role: "Fast retrieval",
    fit: "Fresh web index, low-latency fallback",
    tone: "blue",
  },
  {
    name: "Exa",
    role: "Semantic discovery",
    fit: "Company and people search, rich excerpts",
    tone: "clay",
  },
  {
    name: "Tavily",
    role: "Agent search",
    fit: "Balanced search, extraction, crawl workflows",
    tone: "mint",
  },
  {
    name: "Perplexity",
    role: "Answer synthesis",
    fit: "Cited briefs and web-grounded summaries",
    tone: "violet",
  },
];

const runSteps = [
  {
    title: "Intake",
    body: "CSV, XLSX, or a natural-language research brief enters the workbench.",
    icon: <FileSpreadsheet aria-hidden="true" size={18} />,
  },
  {
    title: "Profile",
    body: "The backend identifies freshness, citations, structure, enrichment, speed, and cost signals.",
    icon: <BrainCircuit aria-hidden="true" size={18} />,
  },
  {
    title: "Route",
    body: "Provider knowledge and benchmark assumptions score the best primary vendor and backup plan.",
    icon: <Route aria-hidden="true" size={18} />,
  },
  {
    title: "Review",
    body: "Rows return with confidence, provider attribution, citations, and export-ready fields.",
    icon: <BadgeCheck aria-hidden="true" size={18} />,
  },
];

const architecture = [
  {
    title: "Next.js surfaces",
    body: "The public page explains the wedge. The workbench handles upload, routing, review, and export.",
    icon: <Layers3 aria-hidden="true" size={18} />,
  },
  {
    title: "FastAPI control plane",
    body: "Python owns provider orchestration, spreadsheet preview, research runs, and CSV/PDF export.",
    icon: <Network aria-hidden="true" size={18} />,
  },
  {
    title: "Provider knowledge",
    body: "Versioned vendor cards capture strengths, tradeoffs, task fit, and source URLs.",
    icon: <Database aria-hidden="true" size={18} />,
  },
  {
    title: "Routing advisor",
    body: "The router returns a strategy: single provider, fallback, verification, synthesis, or manual.",
    icon: <GitBranch aria-hidden="true" size={18} />,
  },
];

const readiness = [
  {
    label: "Pitch-ready",
    title: "The product story is coherent",
    body: "The workflow, buyer pain, provider strategy, and audit trail are easy to explain.",
    status: "ready",
  },
  {
    label: "Prototype-ready",
    title: "Core loop runs end to end",
    body: "Upload, route, cited result shape, and exports exist, with demo-mode fallbacks.",
    status: "ready",
  },
  {
    label: "Demo gap",
    title: "Multi-provider execution is still planned",
    body: "The advisor recommends fallback and verification steps, but execution still runs the primary provider.",
    status: "gap",
  },
  {
    label: "Demo gap",
    title: "Benchmarks need Edna telemetry",
    body: "Current knowledge is researched from docs and public benchmarks. Production routing needs observed outcomes.",
    status: "gap",
  },
];

const signals = [
  ["freshness", "latest news, funding, current signals"],
  ["citations", "sources, evidence, verification"],
  ["structure", "fields, schema, CSV, table output"],
  ["entities", "companies, funds, LPs, contacts"],
  ["speed", "fast, low latency, quick run"],
  ["cost", "budget, cheap, cost control"],
];

export default function PitchPage() {
  return (
    <main className="pitch-page">
      <header className="pitch-nav" aria-label="Pitch navigation">
        <Link className="pitch-brand" href="/">
          <span>ControlThrive</span>
          Edna Search
        </Link>
        <nav aria-label="Pitch sections">
          <a href="#system">System</a>
          <a href="#advisor">Advisor</a>
          <a href="#readiness">Readiness</a>
          <Link href="/workbench">Workbench</Link>
        </nav>
        <span className="pitch-status">Internal pitch map</span>
      </header>

      <section className="pitch-hero" aria-labelledby="pitch-title">
        <div>
          <p className="pitch-kicker">Pre-demo product narrative</p>
          <h1 id="pitch-title">A routing layer for cited capital-formation research.</h1>
          <p>
            Edna Search is shaping into a vendor-selection and evidence-control layer for placement
            agents and private-capital teams. This page explains how the system works without
            overselling it as a finished live demo.
          </p>
          <div className="pitch-actions">
            <a className="pitch-primary" href="#system">
              See system map
              <ArrowRight aria-hidden="true" size={17} />
            </a>
            <Link className="pitch-secondary" href="/workbench">
              Open workbench
            </Link>
          </div>
        </div>

        <div className="pitch-snapshot" aria-label="Product positioning snapshot">
          <div>
            <span>Buyer</span>
            <strong>Placement agents</strong>
          </div>
          <div>
            <span>Workflow</span>
            <strong>Lists to cited rows</strong>
          </div>
          <div>
            <span>Wedge</span>
            <strong>Provider routing</strong>
          </div>
          <div>
            <span>Status</span>
            <strong>Pitch + prototype</strong>
          </div>
        </div>
      </section>

      <section className="pitch-band" aria-label="Product thesis">
        <strong>Core thesis</strong>
        <p>
          Search vendors are not interchangeable. The product should choose the best vendor mix for
          the job, keep sources attached, and let operators export defensible research rows.
        </p>
      </section>

      <section className="pitch-section pitch-system" id="system" aria-labelledby="system-title">
        <SectionHeader
          eyebrow="System map"
          title="How a run moves through Edna Search"
          body="The current product has the core control plane: intake, route selection, provider adapters, cited results, and export."
        />
        <SystemDiagram />
      </section>

      <section className="pitch-section" aria-labelledby="flow-title">
        <SectionHeader
          eyebrow="Run lifecycle"
          title="The operator sees one workflow, but the backend makes the routing decision."
        />
        <div className="pitch-step-grid">
          {runSteps.map((step, index) => (
            <article className="pitch-step" key={step.title}>
              <span className="pitch-step-index">{String(index + 1).padStart(2, "0")}</span>
              <IconBlock>{step.icon}</IconBlock>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="pitch-section pitch-advisor-section" id="advisor" aria-labelledby="advisor-title">
        <SectionHeader
          eyebrow="Routing advisor"
          title="The new branch adds task-fit intelligence before provider selection."
          body="The advisor profiles the prompt, scores provider capabilities, and returns an auditable route plan."
        />
        <div className="pitch-advisor-grid">
          <PromptProfiler />
          <ProviderMatrix />
        </div>
      </section>

      <section className="pitch-section" aria-labelledby="architecture-title">
        <SectionHeader
          eyebrow="Architecture"
          title="A split app with Python owning provider orchestration."
        />
        <div className="pitch-architecture-grid">
          {architecture.map((item) => (
            <article className="pitch-architecture-card" key={item.title}>
              <IconBlock>{item.icon}</IconBlock>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="pitch-section pitch-readiness" id="readiness" aria-labelledby="readiness-title">
        <SectionHeader
          eyebrow="Pitch posture"
          title="What to say honestly before a demo."
          body="This is credible as a product direction and prototype. It should not be pitched as a fully validated automation engine yet."
        />
        <div className="pitch-readiness-grid">
          {readiness.map((item) => (
            <article className="pitch-readiness-card" data-status={item.status} key={item.title}>
              <span>{item.label}</span>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="pitch-close" aria-labelledby="close-title">
        <div>
          <p className="pitch-kicker">The pitch in one sentence</p>
          <h2 id="close-title">
            Edna Search turns messy research lists into cited, provider-routed rows that a capital
            formation team can review and defend.
          </h2>
        </div>
        <a className="pitch-primary" href="mailto:hello@controlthrive.com?subject=Edna%20Search%20pitch">
          Send pitch note
          <ArrowRight aria-hidden="true" size={17} />
        </a>
      </section>
    </main>
  );
}

function SectionHeader({
  body,
  eyebrow,
  title,
}: {
  body?: string;
  eyebrow: string;
  title: string;
}) {
  return (
    <div className="pitch-section-header">
      <p className="pitch-kicker">{eyebrow}</p>
      <h2>{title}</h2>
      {body ? <p>{body}</p> : null}
    </div>
  );
}

function IconBlock({ children }: { children: ReactNode }) {
  return <span className="pitch-icon-block">{children}</span>;
}

function SystemDiagram() {
  return (
    <div className="pitch-diagram" aria-label="System architecture diagram">
      <div className="pitch-diagram-svg" aria-hidden="true">
        <svg viewBox="0 0 1120 420" role="img">
          <defs>
            <marker
              id="pitch-arrow"
              markerHeight="8"
              markerWidth="8"
              orient="auto"
              refX="7"
              refY="4"
            >
              <path d="M0,0 L8,4 L0,8 Z" />
            </marker>
          </defs>
          <path className="pitch-flow-line" d="M145 130 C255 80 320 80 430 130" />
          <path className="pitch-flow-line" d="M145 290 C260 335 320 335 430 290" />
          <path className="pitch-flow-line" d="M550 210 C650 210 690 210 770 210" />
          <path className="pitch-flow-line muted" d="M865 210 C940 135 980 125 1040 108" />
          <path className="pitch-flow-line muted" d="M865 210 C940 210 980 210 1040 210" />
          <path className="pitch-flow-line muted" d="M865 210 C940 286 980 296 1040 312" />
        </svg>
      </div>

      <div className="pitch-node pitch-node-a">
        <IconBlock>
          <FileSpreadsheet aria-hidden="true" size={18} />
        </IconBlock>
        <span>CSV/XLSX</span>
        <strong>Contact list</strong>
      </div>
      <div className="pitch-node pitch-node-b">
        <IconBlock>
          <Search aria-hidden="true" size={18} />
        </IconBlock>
        <span>Brief</span>
        <strong>Natural-language ask</strong>
      </div>
      <div className="pitch-node pitch-node-c">
        <IconBlock>
          <BrainCircuit aria-hidden="true" size={18} />
        </IconBlock>
        <span>FastAPI</span>
        <strong>Prompt profiler</strong>
      </div>
      <div className="pitch-node pitch-node-d">
        <IconBlock>
          <Route aria-hidden="true" size={18} />
        </IconBlock>
        <span>Advisor</span>
        <strong>Route strategy</strong>
      </div>
      <div className="pitch-node pitch-node-e">
        <IconBlock>
          <BadgeCheck aria-hidden="true" size={18} />
        </IconBlock>
        <span>Output</span>
        <strong>Cited rows</strong>
      </div>
      <div className="pitch-provider-cloud">
        {providerRoutes.map((provider) => (
          <div className="pitch-provider-pill" data-tone={provider.tone} key={provider.name}>
            <strong>{provider.name}</strong>
            <span>{provider.role}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PromptProfiler() {
  return (
    <article className="pitch-profiler">
      <div className="pitch-panel-heading">
        <IconBlock>
          <Sparkles aria-hidden="true" size={18} />
        </IconBlock>
        <div>
          <span>Prompt profile</span>
          <h3>Signals extracted from the job</h3>
        </div>
      </div>
      <div className="pitch-signal-list">
        {signals.map(([label, body]) => (
          <div key={label}>
            <strong>{label}</strong>
            <span>{body}</span>
          </div>
        ))}
      </div>
    </article>
  );
}

function ProviderMatrix() {
  return (
    <article className="pitch-provider-matrix">
      <div className="pitch-panel-heading">
        <IconBlock>
          <Building2 aria-hidden="true" size={18} />
        </IconBlock>
        <div>
          <span>Provider fit</span>
          <h3>What each vendor is for</h3>
        </div>
      </div>
      <div className="pitch-matrix-list">
        {providerRoutes.map((provider) => (
          <div key={provider.name}>
            <span data-tone={provider.tone}>{provider.name}</span>
            <strong>{provider.role}</strong>
            <p>{provider.fit}</p>
          </div>
        ))}
      </div>
      <div className="pitch-strategy-row" aria-label="Advisor strategies">
        <span>
          <CheckCircle2 aria-hidden="true" size={14} />
          primary
        </span>
        <span>
          <ShieldCheck aria-hidden="true" size={14} />
          verification
        </span>
        <span>
          <Gauge aria-hidden="true" size={14} />
          fallback
        </span>
        <span>
          <CircleDollarSign aria-hidden="true" size={14} />
          cost guard
        </span>
        <span>
          <Table2 aria-hidden="true" size={14} />
          export
        </span>
        <span>
          <LockKeyhole aria-hidden="true" size={14} />
          audit trail
        </span>
      </div>
    </article>
  );
}
