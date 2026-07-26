import { directedEventText, eventHeading, renderAutonomous } from "./command_center_events";
import { textElement } from "./dom";
import { OperatorClient } from "./operator_client";
import type {
  AgentId,
  AutonomousTaskReceipt,
  DirectedJobEvent,
  Interaction,
  InteractionMode,
} from "./schema";

const agentIds = [
  "opportunity_manager",
  "day_trading",
  "swing_trading",
  "systematic_quant",
  "derivatives_research",
  "market_context",
] as const satisfies readonly AgentId[];

const agentLabels: Readonly<Record<AgentId, readonly [string, string]>> = {
  opportunity_manager: ["기회 관리자", "OPPORTUNITY MANAGER"],
  day_trading: ["데이 트레이딩", "DAY TRADING"],
  swing_trading: ["스윙 트레이딩", "SWING TRADING"],
  systematic_quant: ["시스템 퀀트", "SYSTEMATIC QUANT"],
  derivatives_research: ["파생상품 연구", "DERIVATIVES RESEARCH"],
  market_context: ["시장 맥락", "MARKET CONTEXT"],
};

const modes = [
  ["conversation", "대화"],
  ["research", "연구 작업"],
  ["analysis", "분석 작업"],
  ["hypothesis", "가설 등록"],
  ["experiment", "실험 실행"],
  ["allowed_code", "허용 코드 점검"],
] as const satisfies readonly (readonly [InteractionMode, string])[];

export class AgentWorkspace {
  private readonly interactions = new Map<string, Interaction>();
  private readonly directed = new Map<string, DirectedJobEvent>();
  private readonly autonomous = new Map<string, AutonomousTaskReceipt>();
  private readonly operator: OperatorClient;
  private host: HTMLElement | null = null;
  private selected: AgentId = "opportunity_manager";
  private authenticated = false;
  private connected = false;
  private submitting = false;
  private started = false;

  constructor() {
    this.operator = new OperatorClient({
      onSession: (authenticated) => {
        this.authenticated = authenticated;
        this.render();
      },
      onConnection: (state) => {
        this.connected = state === "connected";
        this.render();
      },
      onInteraction: (interaction) => {
        this.interactions.set(
          interaction.id,
          latestInteraction(this.interactions.get(interaction.id), interaction),
        );
        this.render();
      },
      onDirectedJob: (event) => {
        this.directed.set(`${event.interaction_id}:${event.sequence}:${event.kind}`, event);
        this.render();
      },
      onAutonomousJob: (event) => {
        this.autonomous.set(`${event.public_task_id}:${event.sequence}`, event);
        this.render();
      },
    });
  }

  mount(host: HTMLElement): void {
    this.host = host;
    this.render();
    if (!this.started) {
      this.started = true;
      void this.operator.start();
    }
  }

  private render(): void {
    if (this.host === null) return;
    const section = document.createElement("section");
    section.className = "command-console";
    section.append(this.renderFamilies(), this.renderComposer(), this.renderTimeline());
    this.host.replaceChildren(section);
  }

  private renderFamilies(): HTMLElement {
    const region = document.createElement("div");
    region.className = "command-family-selector";
    region.setAttribute("aria-label", "연구 에이전트 가족");
    for (const agentId of agentIds) {
      const [label, scope] = agentLabels[agentId];
      const button = document.createElement("button");
      button.type = "button";
      button.className = "command-family";
      button.setAttribute("aria-pressed", String(this.selected === agentId));
      button.append(textElement("strong", label), textElement("small", scope));
      button.addEventListener("click", () => {
        this.selected = agentId;
        this.render();
        this.host?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
      });
      region.append(button);
    }
    return region;
  }

  private renderComposer(): HTMLFormElement {
    const form = document.createElement("form");
    form.className = "command-composer";
    const status = !this.authenticated
      ? "조회 전용 · 기기 페어링 필요"
      : this.connected
        ? "운영자 명령 채널 연결됨"
        : "명령 채널 재연결 중";
    const select = document.createElement("select");
    select.id = "command-mode";
    select.disabled = !this.authenticated || !this.connected || this.submitting;
    for (const [value, label] of modes) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
    const textarea = document.createElement("textarea");
    textarea.id = "command-text";
    textarea.name = "command";
    textarea.maxLength = 2_000;
    textarea.rows = 4;
    textarea.required = true;
    textarea.placeholder = `${agentLabels[this.selected][0]}에게 증거 기반 목표를 지시하세요.`;
    textarea.disabled = select.disabled;
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = this.submitting ? "접수 중" : "명령 접수";
    submit.disabled = select.disabled;
    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) form.requestSubmit();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.submit(select.value, textarea.value);
    });
    const heading = document.createElement("div");
    heading.className = "command-composer-heading";
    heading.append(
      textElement(
        "div",
        status,
        `operator-state ${this.connected ? "state-ready" : "state-armed"}`,
      ),
      Object.assign(document.createElement("label"), {
        htmlFor: "command-mode",
        textContent: "채널",
      }),
      select,
    );
    form.append(
      heading,
      Object.assign(document.createElement("label"), {
        htmlFor: "command-text",
        textContent: "운영자 메시지",
      }),
      textarea,
      submit,
    );
    return form;
  }

  private async submit(rawMode: string, rawCommand: string): Promise<void> {
    const mode = modes.find(([candidate]) => candidate === rawMode)?.[0];
    const command = rawCommand.trim();
    if (mode === undefined || command.length === 0 || this.submitting) return;
    this.submitting = true;
    this.render();
    try {
      const interaction = await this.operator.submit(this.selected, mode, command);
      this.interactions.set(interaction.id, interaction);
    } finally {
      this.submitting = false;
      this.render();
    }
  }

  private renderTimeline(): HTMLElement {
    const region = document.createElement("section");
    region.className = "command-timeline";
    region.append(textElement("h2", `${agentLabels[this.selected][0]} 활동 타임라인`));
    const interactions = [...this.interactions.values()]
      .filter((item) => item.agent_id === this.selected)
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
    for (const interaction of interactions) region.append(this.renderInteraction(interaction));
    const autonomous = [...this.autonomous.values()]
      .filter((item) => item.agent_family_id === this.selected)
      .sort((left, right) => right.occurred_at.localeCompare(left.occurred_at));
    for (const task of autonomous) region.append(renderAutonomous(task));
    if (interactions.length + autonomous.length === 0) {
      region.append(textElement("p", "이 가족의 대화나 작업 receipt가 없습니다.", "empty-state"));
    }
    return region;
  }

  private renderInteraction(interaction: Interaction): HTMLElement {
    const article = document.createElement("article");
    const directed = interaction.mode !== "conversation";
    article.className = `command-event command-event-${directed ? "directed" : "conversation"}`;
    article.dataset["channel"] = directed ? "directed-job" : "conversation";
    article.append(
      eventHeading(directed ? "지시형 작업" : "대화", interaction.state, interaction.updated_at),
      textElement("p", interaction.command, "command-request"),
    );
    const events = [...this.directed.values()]
      .filter((event) => event.interaction_id === interaction.id)
      .sort((left, right) => left.sequence - right.sequence);
    for (const event of events) {
      article.append(textElement("p", directedEventText(event), "command-step"));
    }
    if (interaction.response !== null) {
      article.append(textElement("p", interaction.response, "command-response"));
    }
    return article;
  }
}

export function latestInteraction(
  current: Interaction | undefined,
  incoming: Interaction,
): Interaction {
  return current !== undefined && current.updated_at > incoming.updated_at ? current : incoming;
}
