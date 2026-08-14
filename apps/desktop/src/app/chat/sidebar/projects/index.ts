// Public surface of the project/worktree sidebar, consumed by the sidebar root.
export { EnteredProjectContent } from './entered-content'
export {
  arrangeProjectRows,
  hasNestedProjects,
  orderProjectsByIds,
  PROJECT_PREVIEW_COUNT,
  type ProjectOverviewRowEntry,
  projectTreeCwd,
  sortProjectsForOverview,
  useRepoWorktreeMap
} from './model'
export { ProjectBackRow, ProjectOverviewRow } from './overview-row'
export { ProjectMenu } from './project-menu'
export { SidebarWorkspaceGroup } from './workspace-group'
export {
  overlayLiveLanes,
  overlayLivePreviews,
  sessionRecency,
  type SidebarProjectTree,
  type SidebarSessionGroup,
  type SidebarWorkspaceTree
} from './workspace-groups'
export { StartWorkButton } from './workspace-header'
