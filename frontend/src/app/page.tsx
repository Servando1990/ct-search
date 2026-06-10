import {
  ArrowRight,
  BadgeCheck,
  Database,
  FileSpreadsheet,
  LockKeyhole,
  Route,
  Search,
  ShieldCheck,
  Table2,
} from "lucide-react";

const providers = ["Parallel", "Brave", "Exa", "Tavily", "Perplexity"];

const problems = [
  {
    title: "Provider choice is scattered",
    body: "Cost, speed, and confidence are decided in separate tabs, then explained after the fact.",
  },
  {
    title: "Sources disappear",
    body: "Rows get copied into spreadsheets without the citation trail needed for diligence.",
  },
  {
    title: "Exports are manual",
    body: "Teams still clean contact lists by hand before the data reaches CRM.",
  },
];

const features = [
  {
    icon: <FileSpreadsheet aria-hidden="true" size={20} />,
    title: "Spreadsheet-first intake",
    body: "Upload CSV or XLSX lists, or start from a natural-language research brief.",
  },
  {
    icon: <Route aria-hidden="true" size={20} />,
    title: "Failure-cost routing",
    body: "The router plans primary, fallback, and verifier steps from what a wrong answer costs — not vendor brand.",
  },
  {
    icon: <BadgeCheck aria-hidden="true" size={20} />,
    title: "Cited enrichment",
    body: "Every returned field carries provider attribution, confidence, and source context.",
  },
  {
    icon: <Table2 aria-hidden="true" size={20} />,
    title: "Clean exports",
    body: "Send reviewed rows back to your workflow as CSV or PDF.",
  },
];

const rows = [
  ["Northstar LP", "Healthcare", "88%", "Parallel"],
  ["Aster Capital", "Europe", "82%", "Exa"],
  ["Meridian Partners", "Lower middle market", "91%", "Tavily"],
];

export default function Home() {
  return (
    <main className="launch-page">
      <header className="launch-nav" aria-label="Primary navigation">
        <a className="brand-mark" href="#">
          <span>ControlThrive</span>
          Edna Search
        </a>
        <nav aria-label="Page sections">
          <a href="#workflow">Workflow</a>
          <a href="#proof">Proof</a>
          <a href="#security">Security</a>
          <a href="/workbench">Workbench</a>
        </nav>
        <a className="nav-action" href="mailto:hello@controlthrive.com?subject=Edna%20Search%20demo">
          Book demo
        </a>
      </header>

      <section className="hero-section" aria-labelledby="hero-title">
        <div className="hero-copy">
          <h1 id="hero-title">Edna Search</h1>
          <p className="hero-claim">Smart order routing, for research.</p>
          <p className="hero-body">
            Write a brief or attach a list. Edna plans the route — primary, fallback, verifier —
            across five search venues and returns cited, confidence-scored rows, with cost and
            reasoning in the open.
          </p>
          <div className="hero-actions">
            <a className="primary-action" href="mailto:hello@controlthrive.com?subject=Edna%20Search%20demo">
              Book demo
              <ArrowRight aria-hidden="true" size={18} />
            </a>
            <a className="secondary-action" href="/workbench">
              Open workbench
            </a>
          </div>
        </div>

        <div className="product-frame" aria-label="Edna Search product preview">
          <div className="frame-bar">
            <span>new run</span>
            <strong>auto route</strong>
          </div>
          <div className="frame-grid">
            <div className="run-brief">
              <Search aria-hidden="true" size={18} />
              <p>Find LP contacts for healthcare funds in the US and Europe.</p>
            </div>
            <div className="route-stack">
              <div>
                <span>Route</span>
                <strong>Parallel → Exa verify</strong>
              </div>
              <div>
                <span>Per grounded row</span>
                <strong>$0.052</strong>
              </div>
              <div>
                <span>Confidence</span>
                <strong>91%</strong>
              </div>
            </div>
          </div>
          <div className="result-table" role="table" aria-label="Example cited rows">
            <div className="result-row result-head" role="row">
              <span role="columnheader">Firm</span>
              <span role="columnheader">Signal</span>
              <span role="columnheader">Score</span>
              <span role="columnheader">Source</span>
            </div>
            {rows.map((row) => (
              <div className="result-row" role="row" key={row[0]}>
                {row.map((cell) => (
                  <span role="cell" key={cell}>
                    {cell}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="provider-band" aria-label="Supported providers">
        <span>Routes across</span>
        <div>
          {providers.map((provider) => (
            <strong key={provider}>{provider}</strong>
          ))}
        </div>
      </section>

      <section className="problem-section" id="workflow" aria-labelledby="problem-title">
        <div className="section-intro">
          <h2 id="problem-title">Research lists break when routing, sources, and exports split apart.</h2>
        </div>
        <div className="problem-list">
          {problems.map((problem, index) => (
            <article className="problem-item" key={problem.title}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <h3>{problem.title}</h3>
              <p>{problem.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="proof-section" id="proof" aria-labelledby="proof-title">
        <div>
          <h2 id="proof-title">One run. One audit trail.</h2>
        </div>
        <p>
          The workbench keeps the route, confidence, citations, and export shape in the same
          place, so every enriched row can be reviewed — and any row dropped — before it leaves
          the product.
        </p>
      </section>

      <section className="feature-section" aria-label="Product capabilities">
        {features.map((feature) => (
          <article className="feature-row" key={feature.title}>
            <span>{feature.icon}</span>
            <h3>{feature.title}</h3>
            <p>{feature.body}</p>
          </article>
        ))}
      </section>

      <section className="security-section" id="security" aria-labelledby="security-title">
        <div>
          <h2 id="security-title">Citations stay attached.</h2>
        </div>
        <div className="security-points">
          <span>
            <ShieldCheck aria-hidden="true" size={18} />
            Provider attribution
          </span>
          <span>
            <Database aria-hidden="true" size={18} />
            Structured exports
          </span>
          <span>
            <LockKeyhole aria-hidden="true" size={18} />
            Review before CRM
          </span>
        </div>
      </section>

      <section className="final-section" aria-labelledby="final-title">
        <h2 id="final-title">Turn raw lists into cited rows.</h2>
        <a className="primary-action" href="mailto:hello@controlthrive.com?subject=Edna%20Search%20demo">
          Book demo
          <ArrowRight aria-hidden="true" size={18} />
        </a>
      </section>
    </main>
  );
}
