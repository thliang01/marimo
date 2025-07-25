/* Copyright 2024 Marimo. All rights reserved. */

import { useAtomValue } from "jotai";
import type { PackageInstallationStatus } from "@/core/kernel/messages";
import { createReducerAndAtoms } from "@/utils/createReducer";
import { generateUUID } from "@/utils/uuid";

type Identified<T> = { id: string } & T;

export interface MissingPackageAlert {
  kind: "missing";
  packages: string[];
  isolated: boolean;
}

export interface InstallingPackageAlert {
  kind: "installing";
  packages: PackageInstallationStatus;
}

export function isMissingPackageAlert(
  alert: MissingPackageAlert | InstallingPackageAlert,
): alert is MissingPackageAlert {
  return alert.kind === "missing";
}

export function isInstallingPackageAlert(
  alert: MissingPackageAlert | InstallingPackageAlert,
): alert is InstallingPackageAlert {
  return alert.kind === "installing";
}

/** Prominent alerts.
 *
 * Right now we only have one type of alert.
 */
interface AlertState {
  packageAlert:
    | Identified<MissingPackageAlert>
    | Identified<InstallingPackageAlert>
    | null;
}

const { valueAtom: alertAtom, useActions } = createReducerAndAtoms(
  () => ({ packageAlert: null }) as AlertState,
  {
    addPackageAlert: (
      state,
      alert: MissingPackageAlert | InstallingPackageAlert,
    ) => {
      return {
        ...state,
        packageAlert: { id: generateUUID(), ...alert },
      };
    },

    clearPackageAlert: (state, id: string) => {
      return state.packageAlert !== null && state.packageAlert.id === id
        ? { ...state, packageAlert: null }
        : state;
    },
  },
);

/**
 * React hook to get the Alert state.
 */
export const useAlerts = () => useAtomValue(alertAtom);

/**
 * React hook to get the Alerts actions.
 */
export function useAlertActions() {
  return useActions();
}
