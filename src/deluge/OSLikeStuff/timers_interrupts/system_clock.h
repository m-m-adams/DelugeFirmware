//
// Created by Mark Adams on 2024-12-28.
//

#ifndef DELUGE_SYSTEM_CLOCK_H
#define DELUGE_SYSTEM_CLOCK_H
#include "clock_type.h"

#ifdef __cplusplus
extern "C" {
#endif
Time getSecondsFromStart();
void startSystemClock();
#ifdef __cplusplus
}
#endif
#endif // DELUGE_SYSTEM_CLOCK_H
