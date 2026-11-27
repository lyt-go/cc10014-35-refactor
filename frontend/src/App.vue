<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">古建筑修缮管理平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">文保工程师、工匠和监理共同管理古建筑修缮工序，审核修缮方案，签收变更后按确认版施工。</span>
        <span class="head-user">当前值班：应用开发组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "建筑勘查", path: "/building_survey" }, { label: "修缮方案", path: "/restoration_plan" }, { label: "材料核验", path: "/material_verify" }, { label: "工匠分配", path: "/craftsman_assign" }, { label: "工序编排", path: "/work_sequence" }, { label: "质量检查", path: "/quality_inspect" }, { label: "历史图纸", path: "/historical_drawing" }, { label: "构件登记", path: "/component_registry" }, { label: "环境监测", path: "/env_monitor" }, { label: "修缮日志", path: "/restoration_log" }, { label: "验收记录", path: "/acceptance_record" }, { label: "修缮支出", path: "/funding_use" }, { label: "档案归档", path: "/archive_mgmt" }, { label: "专家评审", path: "/expert_review" }, { label: "安全交底", path: "/safety_briefing" }, { label: "施工排期", path: "/construction_schedule" }, { label: "变更签证", path: "/change_order" }, { label: "费用调整台账", path: "/fee_adjustment" }, { label: "竣工总结", path: "/completion_summary" }]
</script>
