type RailProps = { active: 'overview' | 'workflow' | 'trust' | 'reports'; page: string };

export default function Rail({ active, page }: RailProps) {
  return (
    <aside className="rail flex h-full w-[22vw] shrink-0 flex-col px-[3vw] py-[5vh]">
      <div className="mb-[6vh] flex items-center gap-[1vw]"><div className="h-[1.5vw] w-[1.5vw] rounded-[.3vw] bg-[#7AA2F7]" /><div className="text-[1.2vw] font-semibold text-white">variance.assistant</div></div>
      <div className="mb-[2vh] text-[.9vw] font-semibold uppercase tracking-[.05em] text-[#565F89]">Product Brief</div>
      <div className="flex flex-col gap-[1.5vh]"><div className={active === 'overview' ? 'text-[1vw] text-[#7AA2F7]' : 'text-[1vw] text-[#C0CAF5] opacity-60'}>Overview</div><div className={active === 'workflow' ? 'text-[1vw] text-[#7AA2F7]' : 'text-[1vw] text-[#C0CAF5] opacity-60'}>Workflow</div><div className={active === 'trust' ? 'text-[1vw] text-[#7AA2F7]' : 'text-[1vw] text-[#C0CAF5] opacity-60'}>Trust boundaries</div></div>
      <div className="mt-[4vh] mb-[2vh] text-[.9vw] font-semibold uppercase tracking-[.05em] text-[#565F89]">Resources</div>
      <div className="flex flex-col gap-[1.5vh]"><div className="text-[1vw] text-[#C0CAF5] opacity-60">Data sources</div><div className={active === 'reports' ? 'text-[1vw] text-[#7AA2F7]' : 'text-[1vw] text-[#C0CAF5] opacity-60'}>Saved reports</div><div className="text-[1vw] text-[#C0CAF5] opacity-60">Publishing</div></div>
      <div className="mono mt-auto text-[.8vw] text-[#565F89]">{page} / 08</div>
    </aside>
  );
}