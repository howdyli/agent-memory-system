import { create } from 'zustand';
import { workspaceApi, type Workspace } from '../services/api';

interface WorkspaceState {
  workspaces: Workspace[];
  currentWorkspaceId: number | null;
  loading: boolean;
  loadWorkspaces: () => Promise<void>;
  switchWorkspace: (id: number) => Promise<void>;
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  workspaces: [],
  currentWorkspaceId: (() => {
    const v = localStorage.getItem('current_workspace_id');
    return v ? Number(v) : null;
  })(),
  loading: false,

  loadWorkspaces: async () => {
    set({ loading: true });
    try {
      const res = await workspaceApi.list();
      const list: Workspace[] = Array.isArray(res.data) ? res.data : [];
      set({ workspaces: list, loading: false });

      // 如果还没有选中 workspace，默认选第一个
      const current = get().currentWorkspaceId;
      if (!current && list.length > 0) {
        const firstId = list[0].id;
        localStorage.setItem('current_workspace_id', String(firstId));
        set({ currentWorkspaceId: firstId });
      }
    } catch {
      set({ loading: false });
    }
  },

  switchWorkspace: async (id: number) => {
    try {
      await workspaceApi.switchWorkspace(id);
    } catch {
      // 即使后端失败也允许本地切换
    }
    localStorage.setItem('current_workspace_id', String(id));
    set({ currentWorkspaceId: id });
  },
}));
