/*
 *	bit reservoir include file
 *
 *	Copyright (c) 1999 Mark Taylor
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	 See the GNU
 * Library General Public License for more details.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

#ifndef LAME_RESERVOIR_H
#define LAME_RESERVOIR_H

int     ResvFrameBegin(lame_internal_flags * gfc, int *mean_bits);
void    ResvMaxBits(lame_internal_flags * gfc, int mean_bits, int *targ_bits, int *max_bits,
                    int cbr);
void    ResvAdjust(lame_internal_flags * gfc, gr_info const *gi);
void    ResvFrameEnd(lame_internal_flags * gfc, int mean_bits);

#endif /* LAME_RESERVOIR_H */
